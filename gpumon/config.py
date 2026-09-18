"""Runtime configuration.

gpumon runs in two modes that share the same code paths:

* ``local``  - everything runs in-process (SQLite, an asyncio stream, a
  deterministic offline diagnoser). No AWS account or network required.
* ``aws``    - the same interfaces are backed by DynamoDB, Kinesis and
  Amazon Bedrock. Selected automatically when ``ARGUS_MODE=aws``.

The point of the abstraction is that the anomaly-detection, RAG and
workflow logic never knows which backend it is talking to, so the demo you
run locally is the exact code that gets deployed by the CDK stacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    mode: str = field(default_factory=lambda: os.environ.get("ARGUS_MODE", "local"))

    # Storage
    db_path: str = field(default_factory=lambda: os.environ.get("ARGUS_DB", "argus.db"))
    dynamo_table: str = field(default_factory=lambda: os.environ.get("ARGUS_DDB_TABLE", "argus-incidents"))

    # Streaming
    kinesis_stream: str = field(default_factory=lambda: os.environ.get("ARGUS_STREAM", "argus-telemetry"))

    # AI / Bedrock
    bedrock_model: str = field(
        default_factory=lambda: os.environ.get("ARGUS_BEDROCK_MODEL", "anthropic.claude-sonnet-5-20250101-v1:0")
    )
    embed_model: str = field(
        default_factory=lambda: os.environ.get("ARGUS_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
    )
    # Retrieval backend for local mode: "tfidf" (default) or "embedding".
    retriever: str = field(default_factory=lambda: os.environ.get("ARGUS_RETRIEVER", "tfidf"))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))
    # When true, never call out to Bedrock even in aws mode (useful for CI / demos).
    force_offline_ai: bool = field(default_factory=lambda: _flag("ARGUS_OFFLINE_AI"))

    # Detection tuning
    zscore_threshold: float = field(default_factory=lambda: float(os.environ.get("ARGUS_ZSCORE", "3.0")))
    ewma_alpha: float = field(default_factory=lambda: float(os.environ.get("ARGUS_EWMA_ALPHA", "0.3")))
    min_samples: int = field(default_factory=lambda: int(os.environ.get("ARGUS_MIN_SAMPLES", "20")))

    @property
    def is_aws(self) -> bool:
        return self.mode == "aws"

    @property
    def use_bedrock(self) -> bool:
        return self.is_aws and not self.force_offline_ai


settings = Settings()
