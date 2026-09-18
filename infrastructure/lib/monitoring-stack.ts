import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as kinesis from "aws-cdk-lib/aws-kinesis";
import * as apigw from "aws-cdk-lib/aws-apigatewayv2";
import * as cw from "aws-cdk-lib/aws-cloudwatch";

export interface MonitoringStackProps extends cdk.StackProps {
  telemetryStream: kinesis.Stream;
  api: apigw.HttpApi;
}

/**
 * Observability for the monitor itself (who watches the watchmen).
 * A CloudWatch dashboard tracks ingest throughput and iterator age so we can
 * see the platform keeping up with a load spike, plus an alarm on Kinesis
 * consumer lag - the leading indicator that detection is falling behind.
 */
export class MonitoringStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: MonitoringStackProps) {
    super(scope, id, props);

    const dashboard = new cw.Dashboard(this, "OpsDashboard", { dashboardName: "gpumon-ops" });

    const incomingRecords = props.telemetryStream.metricGetRecordsSuccess();
    const iteratorAge = new cw.Metric({
      namespace: "AWS/Kinesis",
      metricName: "GetRecords.IteratorAgeMilliseconds",
      dimensionsMap: { StreamName: props.telemetryStream.streamName },
      statistic: "Maximum",
    });

    dashboard.addWidgets(
      new cw.GraphWidget({ title: "Telemetry ingest", left: [incomingRecords], width: 12 }),
      new cw.GraphWidget({ title: "Consumer lag (iterator age)", left: [iteratorAge], width: 12 })
    );

    new cw.Alarm(this, "ConsumerLagAlarm", {
      metric: iteratorAge,
      threshold: 30_000, // 30s behind = detection lagging
      evaluationPeriods: 3,
      comparisonOperator: cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
      alarmDescription: "Kinesis consumer is falling behind; anomaly detection is lagging",
    });
  }
}
