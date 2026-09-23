import * as cdk from "aws-cdk-lib";
import * as path from "path";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecsp from "aws-cdk-lib/aws-ecs-patterns";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as kinesis from "aws-cdk-lib/aws-kinesis";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as apigw from "aws-cdk-lib/aws-apigatewayv2";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import { KinesisEventSource } from "aws-cdk-lib/aws-lambda-event-sources";

export interface ApiStackProps extends cdk.StackProps {
  vpc: ec2.Vpc;
  incidentsTable: dynamodb.Table;
  telemetryStream: kinesis.Stream;
  ingestQueue: sqs.Queue;
  diagnoseStateMachine: sfn.StateMachine;
}

/**
 * Serving + processing tier.
 *
 * - HTTP API Gateway -> ingestion Lambda -> Kinesis (POST /events).
 * - The FastAPI service (the same `gpumon` code) runs on Fargate behind an
 *   ALB for the dashboard/WebSocket and read APIs, autoscaling on CPU.
 * - A stream-processor Lambda consumes Kinesis, runs anomaly detection, and
 *   starts the Step Functions diagnosis workflow when an incident opens.
 */
export class ApiStack extends cdk.Stack {
  public readonly httpApi: apigw.HttpApi;
  public readonly albDnsName: string;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    // Repo root is the Docker build context so the gpumon package is bundled.
    const repoRoot = path.join(__dirname, "..", "..");
    const lambdaImage = (handler: string) =>
      lambda.DockerImageCode.fromImageAsset(repoRoot, {
        file: "services/processor/Dockerfile.lambda",
        cmd: [handler],
      });

    // --- ingestion Lambda -> Kinesis ---
    const ingest = new lambda.DockerImageFunction(this, "Ingest", {
      code: lambdaImage("handlers.ingest"),
      environment: { ARGUS_MODE: "aws", ARGUS_STREAM: props.telemetryStream.streamName },
    });
    props.telemetryStream.grantWrite(ingest);

    this.httpApi = new apigw.HttpApi(this, "HttpApi", { apiName: "gpumon-api" });
    this.httpApi.addRoutes({
      path: "/events",
      methods: [apigw.HttpMethod.POST],
      integration: new HttpLambdaIntegration("IngestIntegration", ingest),
    });

    // --- stream processor: Kinesis -> anomaly detection -> Step Functions ---
    const processor = new lambda.DockerImageFunction(this, "StreamProcessor", {
      code: lambdaImage("handlers.process_batch"),
      timeout: cdk.Duration.seconds(60),
      environment: {
        ARGUS_MODE: "aws",
        ARGUS_DDB_TABLE: props.incidentsTable.tableName,
        WORKFLOW_ARN: props.diagnoseStateMachine.stateMachineArn,
      },
    });
    processor.addEventSource(
      new KinesisEventSource(props.telemetryStream, {
        startingPosition: lambda.StartingPosition.LATEST,
        batchSize: 500,
        maxBatchingWindow: cdk.Duration.seconds(1),
        parallelizationFactor: 4,
        retryAttempts: 3,
      })
    );
    props.incidentsTable.grantReadWriteData(processor);
    props.diagnoseStateMachine.grantStartExecution(processor);

    // --- FastAPI dashboard/read service on Fargate ---
    const cluster = new ecs.Cluster(this, "Cluster", { vpc: props.vpc });
    const service = new ecsp.ApplicationLoadBalancedFargateService(this, "DashboardService", {
      cluster,
      cpu: 512,
      memoryLimitMiB: 1024,
      desiredCount: 2,
      minHealthyPercent: 50,
      // Roll back automatically if a new task set fails to stabilise.
      circuitBreaker: { rollback: true },
      taskImageOptions: {
        image: ecs.ContainerImage.fromAsset(repoRoot, { file: "services/processor/Dockerfile" }),
        containerPort: 8000,
        environment: { ARGUS_MODE: "aws", ARGUS_DDB_TABLE: props.incidentsTable.tableName },
      },
      publicLoadBalancer: true,
    });
    props.incidentsTable.grantReadData(service.taskDefinition.taskRole);
    service.service.autoScaleTaskCount({ minCapacity: 2, maxCapacity: 10 }).scaleOnCpuUtilization("Cpu", {
      targetUtilizationPercent: 60,
    });

    this.albDnsName = service.loadBalancer.loadBalancerDnsName;
    new cdk.CfnOutput(this, "ApiUrl", { value: this.httpApi.apiEndpoint });
    new cdk.CfnOutput(this, "DashboardUrl", { value: this.albDnsName });
  }
}
