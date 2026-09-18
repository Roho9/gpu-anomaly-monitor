"""Domain models.

gpumon monitors GPU/ML *training* clusters, so the telemetry vocabulary is
deliberately different from a web-service monitor. A single event describes
the state of one GPU (or one training rank) at a point in time.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _now() -> float:
    return time.time()


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Severity(str, Enum):
    info = "INFO"
    warning = "WARNING"
    critical = "CRITICAL"


class Health(str, Enum):
    healthy = "HEALTHY"
    degraded = "DEGRADED"
    down = "DOWN"


class TelemetryEvent(BaseModel):
    """One sample from one GPU on one node in a training job.

    These are the raw signals a real cluster exposes via DCGM / NVML,
    the NCCL profiler, and the job scheduler.
    """

    job: str = Field(..., description="Training job / run name, e.g. 'llama3-70b-sft'")
    node: str = Field(..., description="Host, e.g. 'gb10-node-03'")
    gpu: int = Field(..., ge=0, description="Local GPU index on the node")
    rank: Optional[int] = Field(None, description="Global rank in the distributed job")

    # Utilisation & memory
    sm_util: float = Field(..., ge=0, le=100, description="SM (compute) utilisation %")
    mem_used_gb: float = Field(..., ge=0)
    mem_total_gb: float = Field(..., gt=0)

    # Thermals & power
    temp_c: float = Field(..., description="GPU temperature, Celsius")
    power_w: float = Field(..., ge=0, description="Board power draw, Watts")

    # Health counters
    ecc_errors: int = Field(0, ge=0, description="Correctable+uncorrectable ECC errors this interval")
    xid_error: Optional[int] = Field(None, description="NVIDIA XID error code, if any")

    # Distributed-training signals
    step: Optional[int] = Field(None, description="Optimizer step index")
    step_time_ms: Optional[float] = Field(None, description="Wall time for the last step")
    tokens_per_s: Optional[float] = Field(None, description="Throughput for this rank")
    nccl_allreduce_ms: Optional[float] = Field(None, description="Last all-reduce collective latency")
    fabric_rx_gbps: Optional[float] = Field(None, description="RoCE/IB receive bandwidth")

    timestamp: float = Field(default_factory=_now)

    @property
    def mem_pct(self) -> float:
        return 100.0 * self.mem_used_gb / self.mem_total_gb


class Anomaly(BaseModel):
    id: str = Field(default_factory=lambda: _uid("anom"))
    job: str
    node: str
    gpu: Optional[int] = None
    metric: str
    value: float
    baseline: float
    zscore: float
    severity: Severity
    kind: str = Field(..., description="Detector that fired, e.g. 'straggler', 'ecc', 'oom'")
    message: str
    timestamp: float = Field(default_factory=_now)


class Diagnosis(BaseModel):
    root_cause: str
    confidence: float = Field(..., ge=0, le=1)
    reasoning: str
    recommended_action: str
    similar_incidents: list[str] = Field(default_factory=list)
    runbooks_used: list[str] = Field(default_factory=list)
    model: str = "offline-heuristic"


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: _uid("inc"))
    job: str
    title: str
    severity: Severity
    status: str = "OPEN"  # OPEN | DIAGNOSING | DIAGNOSED | RESOLVED
    anomalies: list[Anomaly] = Field(default_factory=list)
    diagnosis: Optional[Diagnosis] = None
    opened_at: float = Field(default_factory=_now)
    resolved_at: Optional[float] = None

    def summary_signals(self) -> str:
        """Compact, LLM-friendly description of what fired."""
        lines = []
        for a in self.anomalies:
            loc = f"{a.node}/gpu{a.gpu}" if a.gpu is not None else a.node
            lines.append(
                f"- [{a.kind}] {loc}: {a.metric}={a.value:.1f} "
                f"(baseline {a.baseline:.1f}, z={a.zscore:.1f}) -> {a.message}"
            )
        return "\n".join(lines)
