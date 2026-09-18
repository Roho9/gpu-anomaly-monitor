#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { NetworkingStack } from "../lib/networking-stack";
import { DatabaseStack } from "../lib/database-stack";
import { StreamingStack } from "../lib/streaming-stack";
import { AiStack } from "../lib/ai-stack";
import { ApiStack } from "../lib/api-stack";
import { AnalyticsStack } from "../lib/analytics-stack";
import { MonitoringStack } from "../lib/monitoring-stack";

/**
 * "The best GPU anomaly monitoring system ever" - full infrastructure.
 *
 * Stacks are split by concern and wired through props so each can be
 * deployed and reasoned about independently:
 *
 *   networking -> database -> streaming -> ai -> api -> monitoring
 *
 * `cdk deploy --all` reproduces the entire platform; there is no console
 * click-ops anywhere in this project.
 */
const app = new cdk.App();
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
};

const networking = new NetworkingStack(app, "GpuMonNetworking", { env });
const database = new DatabaseStack(app, "GpuMonDatabase", { env });
const streaming = new StreamingStack(app, "GpuMonStreaming", { env });
const ai = new AiStack(app, "GpuMonAi", {
  env,
  incidentsTable: database.incidentsTable,
});
const api = new ApiStack(app, "GpuMonApi", {
  env,
  vpc: networking.vpc,
  incidentsTable: database.incidentsTable,
  telemetryStream: streaming.telemetryStream,
  diagnoseStateMachine: ai.diagnoseStateMachine,
  ingestQueue: streaming.ingestQueue,
});
new AnalyticsStack(app, "GpuMonAnalytics", {
  env,
  telemetryStream: streaming.telemetryStream,
  rawEventsBucket: streaming.rawEventsBucket,
});
new MonitoringStack(app, "GpuMonMonitoring", {
  env,
  telemetryStream: streaming.telemetryStream,
  api: api.httpApi,
});

app.synth();
