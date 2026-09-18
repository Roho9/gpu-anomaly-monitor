import * as cdk from "aws-cdk-lib";
import * as path from "path";
import { Construct } from "constructs";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as sns from "aws-cdk-lib/aws-sns";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as iam from "aws-cdk-lib/aws-iam";

export interface AiStackProps extends cdk.StackProps {
  incidentsTable: dynamodb.Table;
}

/**
 * The incident-processing workflow, as a Step Functions state machine:
 *
 *   GatherContext -> DiagnoseWithBedrock -> PersistIncident -> Notify(SNS)
 *
 * This is the deployed form of `gpumon.workflow.IncidentManager._diagnose`.
 * Splitting it into an explicit state machine gives retries, per-state
 * timeouts and visual execution history for every incident.
 */
export class AiStack extends cdk.Stack {
  public readonly diagnoseStateMachine: sfn.StateMachine;
  public readonly remediationStateMachine: sfn.StateMachine;
  public readonly alertTopic: sns.Topic;
  public readonly approvalTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: AiStackProps) {
    super(scope, id, props);

    this.alertTopic = new sns.Topic(this, "Alerts", { topicName: "gpumon-alerts" });

    // Repo root is the Docker build context so the gpumon package is bundled.
    const repoRoot = path.join(__dirname, "..", "..");
    const fn = (name: string, handler: string) =>
      new lambda.DockerImageFunction(this, name, {
        code: lambda.DockerImageCode.fromImageAsset(repoRoot, {
          file: "services/processor/Dockerfile.lambda",
          cmd: [handler],
        }),
        timeout: cdk.Duration.seconds(30),
        environment: {
          ARGUS_MODE: "aws",
          ARGUS_DDB_TABLE: props.incidentsTable.tableName,
          ARGUS_BEDROCK_MODEL: "anthropic.claude-sonnet-5-20250101-v1:0",
        },
      });

    const gather = fn("GatherContext", "handlers.gather_context");
    const diagnose = fn("Diagnose", "handlers.diagnose");
    const persist = fn("Persist", "handlers.persist_incident");

    // Bedrock RCA needs invoke on the Claude models; RAG retrieval (gather)
    // needs invoke on Titan embeddings.
    const bedrockInvoke = (fnName: lambda.DockerImageFunction, models: string[]) =>
      fnName.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["bedrock:InvokeModel"],
          resources: models.map((m) => `arn:aws:bedrock:*::foundation-model/${m}`),
        })
      );
    bedrockInvoke(diagnose, ["anthropic.*"]);
    bedrockInvoke(gather, ["amazon.titan-embed-text-v2:0"]);
    props.incidentsTable.grantReadWriteData(gather);
    props.incidentsTable.grantReadWriteData(persist);

    const definition = new tasks.LambdaInvoke(this, "GatherContextTask", {
      lambdaFunction: gather,
      outputPath: "$.Payload",
    })
      .next(
        new tasks.LambdaInvoke(this, "DiagnoseTask", {
          lambdaFunction: diagnose,
          outputPath: "$.Payload",
        }).addRetry({ maxAttempts: 2, interval: cdk.Duration.seconds(3), backoffRate: 2 })
      )
      .next(new tasks.LambdaInvoke(this, "PersistTask", { lambdaFunction: persist, outputPath: "$.Payload" }))
      .next(
        new tasks.SnsPublish(this, "NotifyTask", {
          topic: this.alertTopic,
          message: sfn.TaskInput.fromJsonPathAt("$.alert"),
        })
      );

    this.diagnoseStateMachine = new sfn.StateMachine(this, "DiagnoseWorkflow", {
      stateMachineName: "gpumon-incident-workflow",
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.minutes(5),
      tracingEnabled: true,
    });

    // --- Agentic remediation workflow with a human-approval gate ---
    //
    //   Choice(requires_approval)
    //     true  -> RequestApproval (SNS, waitForTaskToken) -> ApplyRemediation
    //     false -> ApplyRemediation           (low-risk auto-remediation)
    //
    // An operator approves via the dashboard, which calls SendTaskSuccess with
    // the task token to resume the paused execution.
    this.approvalTopic = new sns.Topic(this, "ApprovalRequests", { topicName: "gpumon-approvals" });
    const applyFn = fn("ApplyRemediation", "handlers.apply_remediation");
    props.incidentsTable.grantReadWriteData(applyFn);

    const applyTask = new tasks.LambdaInvoke(this, "ApplyRemediationTask", {
      lambdaFunction: applyFn,
      outputPath: "$.Payload",
    });
    const requestApproval = new tasks.SnsPublish(this, "RequestApproval", {
      topic: this.approvalTopic,
      integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
      message: sfn.TaskInput.fromObject({
        taskToken: sfn.JsonPath.taskToken,
        incidentId: sfn.JsonPath.stringAt("$.incident.id"),
        title: sfn.JsonPath.stringAt("$.proposal.title"),
        risk: sfn.JsonPath.stringAt("$.proposal.risk"),
      }),
      timeout: cdk.Duration.hours(1),
    }).next(applyTask);

    const remediationDefinition = new sfn.Choice(this, "NeedsApproval")
      .when(sfn.Condition.booleanEquals("$.proposal.requires_approval", true), requestApproval)
      .otherwise(applyTask);

    this.remediationStateMachine = new sfn.StateMachine(this, "RemediationWorkflow", {
      stateMachineName: "gpumon-remediation-workflow",
      definitionBody: sfn.DefinitionBody.fromChainable(remediationDefinition),
      timeout: cdk.Duration.hours(2),
      tracingEnabled: true,
    });

    new cdk.CfnOutput(this, "AlertTopicArn", { value: this.alertTopic.topicArn });
    new cdk.CfnOutput(this, "ApprovalTopicArn", { value: this.approvalTopic.topicArn });
  }
}
