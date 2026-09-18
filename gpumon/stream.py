"""Event stream abstraction.

Locally this is an ``asyncio.Queue`` that mirrors the semantics we rely on
from Kinesis: ordered per-partition delivery and fan-out to consumers. In
AWS mode the same ``publish`` call puts records on a Kinesis data stream and
the stream processor Lambda is the consumer.

Keeping the interface tiny (``publish`` / ``subscribe``) is what lets the
rest of the system stay backend-agnostic.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from .config import settings
from .models import TelemetryEvent


class EventStream:
    """In-process pub/sub with Kinesis-like fan-out."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[TelemetryEvent]] = []
        self._published = 0

    @property
    def published(self) -> int:
        return self._published

    async def publish(self, event: TelemetryEvent) -> None:
        self._published += 1
        if settings.is_aws:
            await self._publish_kinesis(event)
        for q in list(self._subscribers):
            # Bounded queues drop-oldest under backpressure, matching how we
            # size shards + a DLQ in the real deployment.
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[TelemetryEvent]:
        q: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=10_000)
        self._subscribers.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.remove(q)

    async def _publish_kinesis(self, event: TelemetryEvent) -> None:  # pragma: no cover - needs AWS
        import boto3  # imported lazily so local mode has no boto3 dependency

        client = boto3.client("kinesis", region_name=settings.aws_region)
        # Partition by node so all GPUs on a host land on the same shard and
        # stay time-ordered for the straggler detector.
        client.put_record(
            StreamName=settings.kinesis_stream,
            PartitionKey=event.node,
            Data=json.dumps(event.model_dump()).encode(),
        )


# Process-wide singleton for the local runtime.
stream = EventStream()
