# Architecture

## Data flow

```
 GPU nodes / DCGM / NCCL profiler
              │  POST /events  (per-GPU telemetry)
              ▼
        API Gateway ──► Ingestion Lambda ──► Kinesis (partition = node)
                                                   │
                                                   ▼
                                         Stream Processor Lambda
                                                   │  anomaly detection
                                                   │  (EWMA z-score + GPU rules)
                                                   ▼
                                            EventBridge / SQS
                                                   │  incident opened
                                                   ▼
                                      Step Functions workflow
                        ┌──────────────┬───────────────┬───────────────┐
                        ▼              ▼               ▼               ▼
                 GatherContext   Diagnose(Bedrock)  Persist(DynamoDB)  Notify(SNS)
                 RAG over            Claude RCA       incident record   email/SMS/Slack
                 runbooks +
                 past incidents
                                                   │
                        Firehose ──► S3 (raw events) ──► Athena (historical queries)
                                                   │
                        CloudFront + S3 (React UI) ◄── read APIs / WebSocket (Fargate)
                                                   │
                              CloudWatch + X-Ray (observe the monitor)
```

## Why GPU/ML clusters, not web services

A generic monitor watches latency and HTTP status. Distributed training has a
different and more interesting failure surface, and the signals are
*cross-rank* rather than per-request:

- **Stragglers** - one slow rank stalls every synchronous `all-reduce`, so the
  whole job's throughput collapses to the slowest GPU. Detected by comparing a
  rank's step time to the cluster median, not to a fixed threshold.
- **Hardware faults** - ECC/HBM errors and XID codes are categorical signals
  that predict imminent crashes and gradient corruption.
- **Fabric stalls** - NCCL/RoCE collective latency spikes cluster-wide and
  points at the interconnect, not any single accelerator.
- **HBM OOM, thermal throttling, input-bound throughput collapse.**

## Detection design

Two layers (see `gpumon/detectors.py`):

1. **Online statistical baselines.** A per-series EWMA mean/variance tracker
   yields a streaming z-score in O(1) per sample with no history buffer, so it
   holds at 100k+ events/sec. Each series learns its own normal, so different
   accelerator generations coexist without retuning thresholds.
2. **GPU-specific rules.** Hardware/collective failure modes that are
   categorical or cross-rank and don't fit a single-series z-score.

## RAG-grounded root-cause analysis

When an incident opens, the workflow retrieves the most relevant runbooks and
the most similar past incidents (`gpumon/rca/rag.py`), then asks Claude on
Bedrock to diagnose using that grounding (`gpumon/rca/bedrock.py`). Resolved
incidents are fed back into the corpus, so the system gets better at
recognising recurring problems over time. Locally a deterministic diagnoser
stands in for Bedrock so the whole flow runs with no credentials.

## Local ⇄ AWS parity

Every local component implements the same interface as its AWS counterpart, so
`make demo` exercises the deployed logic:

| Concern | Local | AWS |
|--------|-------|-----|
| Stream | `EventStream` (asyncio) | Kinesis |
| Store | SQLite single-table | DynamoDB single-table |
| Workflow | `IncidentManager` | Step Functions |
| RCA LLM | offline heuristic | Bedrock (Claude) |
| Embeddings | TF-IDF | Bedrock Titan |
| Alerts | WebSocket broadcast | SNS |

## Tradeoffs worth discussing

- **On-demand everything** (Kinesis on-demand, DynamoDB pay-per-request,
  Fargate autoscaling) keeps idle cost near zero and absorbs a 1k → 25k
  events/sec spike without manual resharding, at a higher per-unit price than
  provisioned capacity.
- **Partition by node** keeps a host's GPUs time-ordered for straggler
  detection, at the cost of potential hot shards for very large nodes.
- **Stateless detection in Lambda** trades baseline continuity across cold
  starts for elastic scale; production checkpoints baselines to a store.
- **Drop-oldest backpressure** favours fresh telemetry over completeness; a DLQ
  captures anything that cannot be processed.
