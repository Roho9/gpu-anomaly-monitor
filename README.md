# The best GPU anomaly monitoring system ever

AI-powered, real-time incident monitoring for **distributed GPU/ML training
clusters**. It ingests per-GPU telemetry, detects the failure modes that
actually take down large training runs (stragglers, ECC/XID hardware faults,
HBM OOM, NCCL/RoCE fabric stalls, thermal throttling, throughput collapse),
uses a RAG-grounded LLM to diagnose the root cause, alerts on it, and shows it
all on a live dashboard.

It is a smaller, GPU-native cousin of Datadog + PagerDuty, built to run **free
and offline in one command** while being **deploy-ready to AWS** via CDK. The
code you run locally is the code that deploys: every local component implements
the same interface as its AWS counterpart.

```bash
make install
make demo        # then open http://127.0.0.1:8000
```

That starts the monitoring pipeline plus a built-in cluster simulator that
drives a 32-GPU job through a timeline of realistic failures so you can watch
detection -> incident -> AI root-cause -> alert -> auto-resolve, end to end.

## Why this is not another CRUD AWS project

Most monitoring demos watch web-service latency and HTTP 500s. Training
clusters fail differently, and the interesting signals are **cross-rank**:

- **Stragglers** - one slow rank stalls every synchronous `all-reduce`, so the
  whole job's throughput collapses to the slowest GPU. Caught by comparing a
  rank to the cluster median, not a fixed threshold.
- **Hardware faults** - accumulating ECC errors and NVIDIA XID codes predict
  imminent crashes and silent gradient corruption.
- **Fabric stalls** - NCCL/RoCE collective latency spiking cluster-wide points
  at the interconnect, not any single GPU.
- **HBM OOM, thermal throttling, input-bound throughput collapse.**

## How it works

```
telemetry -> stream -> anomaly detection -> incident correlation
          -> RAG retrieval -> LLM diagnosis -> alert -> dashboard / auto-resolve
```

**Detection** (`gpumon/detectors.py`) has two layers: an online EWMA
mean/variance tracker that produces a streaming z-score per metric in O(1) (so
it holds at 100k+ events/sec and learns each job's own normal), plus
GPU-specific rules for the categorical and cross-rank failures a z-score cannot
express.

**Root-cause analysis** (`gpumon/rca/`) retrieves the most relevant runbooks
and the most similar past incidents, then asks Claude (on Amazon Bedrock) to
diagnose *grounded in that context*. Resolved incidents are fed back into the
retrieval corpus, so the system improves at recognising recurring problems.
Offline, a deterministic diagnoser stands in for Bedrock so the full flow runs
with zero credentials.

## Measured throughput

The detection pipeline is benchmarked in CI (`python -m gpumon loadtest`).
Single-threaded, in-process, on a laptop:

```
processed 200,000 events in 1.36s
throughput: ~147,000 events/sec
per-event latency: ~6.8 us
```

Partitioning telemetry by node lets this scale horizontally across Kinesis
shards / Lambda consumers; the local number is the per-core floor.

## Local ⇄ AWS parity

| Concern | Local (`make demo`) | AWS (`cdk deploy`) |
|--------|----------------------|--------------------|
| Ingest API | FastAPI `POST /events` | API Gateway + Lambda |
| Stream | `EventStream` (asyncio) | Kinesis (on-demand) |
| Detection | in-process engine | Stream-processor Lambda |
| Workflow | `IncidentManager` | Step Functions |
| RCA LLM | offline heuristic | Bedrock (Claude) |
| Store | SQLite single-table | DynamoDB single-table |
| Alerts | WebSocket broadcast | SNS (email/SMS) |
| Dashboard | FastAPI + WebSocket | Fargate behind ALB / S3 + CloudFront |
| History/analytics | partitioned JSONL archive + query API | Firehose -> S3 (Parquet) -> Athena |
| Embeddings | TF-IDF / local hashing | Bedrock Titan |
| Observability | logs | CloudWatch + X-Ray |

See [`docs/architecture.md`](docs/architecture.md) for the full diagram and the
engineering tradeoffs, and [`infrastructure/`](infrastructure/) for the CDK.

## Project layout

```
gpumon/               # the platform (one implementation, two runtimes)
  models.py           #   GPU/ML telemetry + incident domain models
  detectors.py        #   EWMA z-score baselines + GPU-specific rules
  stream.py store.py  #   Kinesis / DynamoDB abstractions (local + aws)
  workflow.py         #   incident lifecycle (Step Functions equivalent)
  rca/                #   RAG retrieval + Bedrock diagnosis (+ offline)
  api.py              #   FastAPI: ingest, health, incidents, live WebSocket
  dashboard.html      #   live dashboard
  simulator.py        #   cluster telemetry + injectable failure scenarios
runbooks/             # RAG knowledge base (GPU failure runbooks)
infrastructure/       # AWS CDK (TypeScript): 6 stacks, cdk deploy --all
services/processor/   # Lambda handlers + container image (reuse gpumon)
tests/                # pytest suite
```

## Commands

```bash
make demo        # dashboard + built-in cluster simulator
make serve       # server only; feed it via POST /events
make simulate    # drive telemetry at a running server over HTTP
make loadtest    # detector throughput benchmark
make test        # pytest
make synth       # cdk synth the infrastructure
```

Inject a specific failure against a running server:

```bash
python -m gpumon simulate            # runs the demo failure timeline
```

## Deploy to AWS

```bash
cd infrastructure
npm install
npx cdk bootstrap
npx cdk deploy --all
```

Or emulate locally with LocalStack (`cdklocal deploy --all`) - see
[`infrastructure/README.md`](infrastructure/README.md).

## Tech stack

Python 3.10+ / FastAPI / pydantic, AWS CDK (TypeScript), Kinesis, DynamoDB,
Step Functions, Amazon Bedrock (Claude) + RAG, SQS, SNS, API Gateway,
ECS Fargate, S3 + Athena, CloudWatch + X-Ray, Docker, GitHub Actions.

## Roadmap

Built as a vertical slice first, then deepened layer by layer:

- [x] End-to-end vertical slice: ingest -> detect -> diagnose -> alert -> dashboard
- [x] CDK stacks for the full AWS architecture
- [x] Pluggable semantic retrieval: Bedrock Titan embeddings (AWS) with a
      deterministic local embedder; `ARGUS_RETRIEVER=embedding` to try it offline
- [x] Firehose -> S3 -> Athena historical lake + dashboard analytics panel
      (mirrored locally by a partitioned archive + `/history` query API)
- [ ] React + CloudFront frontend replacing the bundled dashboard
- [ ] Agentic auto-remediation (approval-gated rollback / cordon actions)

## License

MIT
