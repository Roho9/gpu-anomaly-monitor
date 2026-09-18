# Infrastructure (AWS CDK)

Everything is Infrastructure as Code. There is no console click-ops: `cdk deploy`
reproduces the entire platform.

## Stacks

| Stack | Responsibility | Key resources |
|-------|----------------|---------------|
| `networking-stack` | VPC + private networking | VPC, NAT, S3/DynamoDB/Kinesis/Bedrock endpoints |
| `database-stack`   | Incident storage | DynamoDB single-table + `byStatus` GSI |
| `streaming-stack`  | Real-time backbone | Kinesis (on-demand), SQS + DLQ, raw-events S3 |
| `ai-stack`         | Incident workflow | Step Functions, Bedrock-invoking Lambda, SNS |
| `api-stack`        | Serving + processing | API Gateway, ingestion Lambda, Kinesis processor, Fargate dashboard |
| `monitoring-stack` | Observe the monitor | CloudWatch dashboard + consumer-lag alarm |

## Deploy

```bash
npm install
npx cdk bootstrap            # once per account/region
npx cdk deploy --all
```

## Local emulation (no AWS account)

The same stacks synthesize and deploy against [LocalStack](https://localstack.cloud):

```bash
localstack start
npx cdklocal bootstrap
npx cdklocal deploy --all
```

## Map to the application code

Each deployed resource has a local counterpart so the code you run with
`make demo` is the code that deploys:

| Local (`gpumon`) | AWS |
|------------------|-----|
| `stream.EventStream` | Kinesis data stream |
| `store.IncidentStore` (SQLite) | DynamoDB single table |
| `workflow.IncidentManager` | Step Functions state machine |
| `rca.bedrock` offline path | Bedrock `InvokeModel` (Claude) |
| in-process alert broadcast | SNS topic |
