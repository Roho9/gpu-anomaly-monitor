import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";

/**
 * DynamoDB single-table design mirroring `gpumon.store`:
 *   PK = JOB#<job>, SK = INCIDENT#<opened_at>#<id>
 * A GSI on status supports "list all open incidents" without a scan.
 * On-demand billing keeps idle cost at zero, matching the local-first ethos.
 */
export class DatabaseStack extends cdk.Stack {
  public readonly incidentsTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.incidentsTable = new dynamodb.Table(this, "Incidents", {
      tableName: "gpumon-incidents",
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.incidentsTable.addGlobalSecondaryIndex({
      indexName: "byStatus",
      partitionKey: { name: "status", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
    });

    new cdk.CfnOutput(this, "IncidentsTableName", { value: this.incidentsTable.tableName });
  }
}
