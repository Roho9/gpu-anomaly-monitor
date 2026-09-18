"""Historical telemetry archive + analytics.

Mirrors the Firehose -> S3 -> Athena path from the AWS deployment. In AWS,
Kinesis records fan out to Firehose, which lands partitioned objects in S3
that Athena queries with SQL. Locally we append the same rollup records to
date-partitioned JSONL files (the "S3 objects") and answer the same questions
by scanning them (the "Athena queries").

Telemetry rollups (per job, per heartbeat) come from here; incident-level
analytics come from the incident store (DynamoDB in AWS).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .store import store

ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "history"


class HistoryArchive:
    def __init__(self, directory: Path | None = None) -> None:
        self.dir = directory or ARCHIVE_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def _partition(self, ts: float) -> Path:
        # dt=YYYY-MM-DD partitioning, exactly how the objects are laid out in S3.
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        return self.dir / f"rollups-{day}.jsonl"

    def record(self, rollup: dict) -> None:
        rollup = {"ts": rollup.get("ts", time.time()), **rollup}
        with self._partition(rollup["ts"]).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rollup) + "\n")

    def _scan(self, since_ts: float):
        for path in sorted(self.dir.glob("rollups-*.jsonl")):
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("ts", 0) >= since_ts:
                        yield rec

    # -- "Athena" queries ----------------------------------------------------
    def timeseries(self, job: str, minutes: int = 30) -> list[dict]:
        since = time.time() - minutes * 60
        return [r for r in self._scan(since) if r.get("job") == job]

    def incident_summary(self) -> dict:
        """Analytics over the incident history (DynamoDB in AWS)."""
        incidents = store.list_incidents(limit=500)
        by_kind: dict[str, int] = {}
        resolve_times: list[float] = []
        for inc in incidents:
            kind = inc.anomalies[0].kind if inc.anomalies else "unknown"
            by_kind[kind] = by_kind.get(kind, 0) + 1
            if inc.resolved_at:
                resolve_times.append(inc.resolved_at - inc.opened_at)
        mttr = sum(resolve_times) / len(resolve_times) if resolve_times else 0.0
        return {
            "total_incidents": len(incidents),
            "resolved": len(resolve_times),
            "by_kind": dict(sorted(by_kind.items(), key=lambda x: x[1], reverse=True)),
            "mean_time_to_resolve_s": round(mttr, 1),
        }


archive = HistoryArchive()
