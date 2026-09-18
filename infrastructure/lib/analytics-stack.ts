import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as kinesis from "aws-cdk-lib/aws-kinesis";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as iam from "aws-cdk-lib/aws-iam";
import * as firehose from "aws-cdk-lib/aws-kinesisfirehose";
import * as glue from "aws-cdk-lib/aws-glue";
import * as athena from "aws-cdk-lib/aws-athena";

export interface AnalyticsStackProps extends cdk.StackProps {
  telemetryStream: kinesis.Stream;
  rawEventsBucket: s3.Bucket;
}

/**
 * Long-term telemetry lake and ad-hoc analytics.
 *
 *   Kinesis --(Firehose)--> S3 (Parquet, dt-partitioned) --> Athena (SQL)
 *
 * This is the deployed form of `gpumon.archive`: Firehose lands raw telemetry
 * in S3 as Parquet partitioned by date, a Glue table describes the schema, and
 * Athena answers the historical questions the dashboard's analytics panel asks.
 */
export class AnalyticsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AnalyticsStackProps) {
    super(scope, id, props);

    // --- Glue catalog: database + telemetry table ---
    const db = new glue.CfnDatabase(this, "GlueDb", {
      catalogId: this.account,
      databaseInput: { name: "gpumon" },
    });

    const cols = (pairs: [string, string][]) => pairs.map(([name, type]) => ({ name, type }));
    const telemetryTable = new glue.CfnTable(this, "TelemetryTable", {
      catalogId: this.account,
      databaseName: "gpumon",
      tableInput: {
        name: "telemetry",
        tableType: "EXTERNAL_TABLE",
        partitionKeys: [{ name: "dt", type: "string" }],
        parameters: { classification: "parquet" },
        storageDescriptor: {
          location: `s3://${props.rawEventsBucket.bucketName}/telemetry/`,
          inputFormat: "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
          outputFormat: "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
          serdeInfo: { serializationLibrary: "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe" },
          columns: cols([
            ["job", "string"], ["node", "string"], ["gpu", "int"], ["rank", "int"],
            ["sm_util", "double"], ["mem_used_gb", "double"], ["mem_total_gb", "double"],
            ["temp_c", "double"], ["power_w", "double"], ["ecc_errors", "int"], ["xid_error", "int"],
            ["step", "int"], ["step_time_ms", "double"], ["tokens_per_s", "double"],
            ["nccl_allreduce_ms", "double"], ["fabric_rx_gbps", "double"], ["timestamp", "double"],
          ]),
        },
      },
    });
    telemetryTable.addDependency(db);

    // --- Firehose: Kinesis source -> S3 (Parquet via the Glue table) ---
    const role = new iam.Role(this, "FirehoseRole", {
      assumedBy: new iam.ServicePrincipal("firehose.amazonaws.com"),
    });
    props.rawEventsBucket.grantReadWrite(role);
    props.telemetryStream.grantRead(role);
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"],
        resources: ["*"],
      })
    );

    new firehose.CfnDeliveryStream(this, "TelemetryFirehose", {
      deliveryStreamName: "gpumon-telemetry-lake",
      deliveryStreamType: "KinesisStreamAsSource",
      kinesisStreamSourceConfiguration: {
        kinesisStreamArn: props.telemetryStream.streamArn,
        roleArn: role.roleArn,
      },
      extendedS3DestinationConfiguration: {
        bucketArn: props.rawEventsBucket.bucketArn,
        roleArn: role.roleArn,
        prefix: "telemetry/dt=!{timestamp:yyyy-MM-dd}/",
        errorOutputPrefix: "telemetry-errors/",
        bufferingHints: { intervalInSeconds: 60, sizeInMBs: 128 },
        dataFormatConversionConfiguration: {
          enabled: true,
          inputFormatConfiguration: { deserializer: { openXJsonSerDe: {} } },
          outputFormatConfiguration: { serializer: { parquetSerDe: {} } },
          schemaConfiguration: {
            catalogId: this.account,
            databaseName: "gpumon",
            tableName: "telemetry",
            roleArn: role.roleArn,
            region: this.region,
          },
        },
      },
    });

    // --- Athena workgroup + example historical queries ---
    new athena.CfnWorkGroup(this, "WorkGroup", {
      name: "gpumon",
      recursiveDeleteOption: true,
      workGroupConfiguration: {
        resultConfiguration: { outputLocation: `s3://${props.rawEventsBucket.bucketName}/athena-results/` },
      },
    });

    new athena.CfnNamedQuery(this, "ThroughputByHour", {
      database: "gpumon",
      workGroup: "gpumon",
      name: "throughput_p50_per_hour",
      description: "Median tokens/sec per job per hour",
      queryString: `
        SELECT job, date_trunc('hour', from_unixtime(timestamp)) AS hour,
               approx_percentile(tokens_per_s, 0.5) AS p50_tokens_per_s
        FROM gpumon.telemetry
        GROUP BY 1, 2 ORDER BY 2 DESC;`,
    });

    new athena.CfnNamedQuery(this, "HotGpus", {
      database: "gpumon",
      workGroup: "gpumon",
      name: "gpus_over_thermal_limit",
      description: "GPUs that spent time above the thermal-throttle threshold",
      queryString: `
        SELECT node, gpu, count(*) AS samples_over_87c, max(temp_c) AS peak_temp
        FROM gpumon.telemetry
        WHERE temp_c >= 87
        GROUP BY 1, 2 ORDER BY 3 DESC;`,
    });

    new cdk.CfnOutput(this, "GlueDatabase", { value: "gpumon" });
  }
}
