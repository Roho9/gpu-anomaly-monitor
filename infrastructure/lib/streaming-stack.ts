import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as kinesis from "aws-cdk-lib/aws-kinesis";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as s3 from "aws-cdk-lib/aws-s3";

/**
 * Real-time telemetry backbone.
 *
 * - Kinesis data stream carries per-GPU telemetry, partitioned by node so a
 *   host's GPUs stay time-ordered for the straggler detector (same guarantee
 *   the local EventStream provides).
 * - An SQS queue in front of ingestion absorbs bursts and decouples the API
 *   from the stream, with a dead-letter queue for poison records.
 * - A raw-events bucket is the long-term store queried later with Athena.
 */
export class StreamingStack extends cdk.Stack {
  public readonly telemetryStream: kinesis.Stream;
  public readonly ingestQueue: sqs.Queue;
  public readonly rawEventsBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.telemetryStream = new kinesis.Stream(this, "Telemetry", {
      streamName: "gpumon-telemetry",
      // On-demand scales shards with load so a 1k -> 25k events/sec spike is
      // absorbed without manual resharding.
      streamMode: kinesis.StreamMode.ON_DEMAND,
      encryption: kinesis.StreamEncryption.MANAGED,
    });

    const dlq = new sqs.Queue(this, "IngestDLQ", { queueName: "gpumon-ingest-dlq" });
    this.ingestQueue = new sqs.Queue(this, "Ingest", {
      queueName: "gpumon-ingest",
      visibilityTimeout: cdk.Duration.seconds(60),
      deadLetterQueue: { queue: dlq, maxReceiveCount: 5 },
    });

    this.rawEventsBucket = new s3.Bucket(this, "RawEvents", {
      bucketName: `gpumon-raw-events-${this.account}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      lifecycleRules: [{ transitions: [{ storageClass: s3.StorageClass.INFREQUENT_ACCESS, transitionAfter: cdk.Duration.days(30) }] }],
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    new cdk.CfnOutput(this, "StreamName", { value: this.telemetryStream.streamName });
  }
}
