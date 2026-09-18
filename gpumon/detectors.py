"""Anomaly detection tuned for GPU/ML training clusters.

Two complementary layers:

1. **Statistical baselines** - a per-series EWMA mean/variance tracker gives a
   robust online z-score for continuous metrics (step time, throughput,
   collective latency, temperature). It adapts to a job's own normal instead
   of relying on fixed thresholds, so a 400 W H100 and a 700 W GB10 each get
   their own baseline.

2. **GPU-specific rules** - hardware/collective failure modes that are
   categorical or cross-rank and don't fit a single-series z-score:
   ECC/XID hardware faults, HBM OOM, thermal throttling, and *stragglers*
   (one rank falling behind its peers, the classic silent killer of
   distributed training throughput).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict, deque
from typing import Optional

from .config import settings
from .models import Anomaly, Severity, TelemetryEvent


class RollingStat:
    """Online EWMA mean and variance -> streaming z-score.

    Uses West's incremental EWMA variance so a single sample update is O(1)
    and no history buffer is needed (matters at 10k+ events/sec).
    """

    def __init__(self, alpha: float) -> None:
        self.alpha = alpha
        self.mean = 0.0
        self.var = 0.0
        self.n = 0

    def update(self, x: float) -> None:
        self.n += 1
        if self.n == 1:
            self.mean = x
            return
        delta = x - self.mean
        self.mean += self.alpha * delta
        self.var = (1 - self.alpha) * (self.var + self.alpha * delta * delta)

    @property
    def std(self) -> float:
        return math.sqrt(self.var)

    def zscore(self, x: float) -> float:
        if self.std < 1e-9:
            return 0.0
        return (x - self.mean) / self.std


# Continuous metrics we baseline, and whether a spike is high-bad, low-bad, or both.
_CONTINUOUS = {
    "step_time_ms": "high",
    "nccl_allreduce_ms": "high",
    "temp_c": "high",
    "tokens_per_s": "low",
    "fabric_rx_gbps": "low",
}


class DetectorEngine:
    def __init__(self) -> None:
        self._stats: dict[tuple, RollingStat] = defaultdict(
            lambda: RollingStat(settings.ewma_alpha)
        )
        # latest step time per rank, for cross-rank straggler comparison
        self._rank_step: dict[str, dict[tuple, float]] = defaultdict(dict)
        # recent ECC totals per GPU to detect a rising trend, not a one-off
        self._ecc_window: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))

    # -- public API ----------------------------------------------------------
    def process(self, e: TelemetryEvent) -> list[Anomaly]:
        found: list[Anomaly] = []
        found += self._statistical(e)
        found += self._hardware_rules(e)
        found += self._memory_rules(e)
        straggler = self._straggler(e)
        if straggler:
            found.append(straggler)
        return found

    # -- layer 1: statistical baselines --------------------------------------
    def _statistical(self, e: TelemetryEvent) -> list[Anomaly]:
        out: list[Anomaly] = []
        for metric, direction in _CONTINUOUS.items():
            value = getattr(e, metric, None)
            if value is None:
                continue
            key = (e.job, e.node, e.gpu, metric)
            stat = self._stats[key]
            z = stat.zscore(value) if stat.n >= settings.min_samples else 0.0
            stat.update(value)
            fired = (direction == "high" and z >= settings.zscore_threshold) or (
                direction == "low" and z <= -settings.zscore_threshold
            )
            if fired:
                out.append(
                    Anomaly(
                        job=e.job,
                        node=e.node,
                        gpu=e.gpu,
                        metric=metric,
                        value=value,
                        baseline=stat.mean,
                        zscore=z,
                        severity=Severity.critical if abs(z) >= 5 else Severity.warning,
                        kind=_metric_kind(metric),
                        message=_describe(metric, value, stat.mean, z),
                    )
                )
        return out

    # -- layer 2: GPU hardware rules -----------------------------------------
    def _hardware_rules(self, e: TelemetryEvent) -> list[Anomaly]:
        out: list[Anomaly] = []
        key = (e.job, e.node, e.gpu)

        if e.ecc_errors:
            self._ecc_window[key].append(e.ecc_errors)
        total_ecc = sum(self._ecc_window[key])
        if total_ecc >= 5:
            out.append(
                Anomaly(
                    job=e.job, node=e.node, gpu=e.gpu, metric="ecc_errors",
                    value=float(total_ecc), baseline=0.0, zscore=float(total_ecc),
                    severity=Severity.critical, kind="ecc",
                    message=f"{total_ecc} ECC errors accumulating on {e.node}/gpu{e.gpu} "
                            f"- failing HBM likely, drain the node",
                )
            )

        if e.xid_error is not None:
            out.append(
                Anomaly(
                    job=e.job, node=e.node, gpu=e.gpu, metric="xid_error",
                    value=float(e.xid_error), baseline=0.0, zscore=99.0,
                    severity=Severity.critical, kind="xid",
                    message=f"NVIDIA XID {e.xid_error} on {e.node}/gpu{e.gpu} "
                            f"- {_xid_hint(e.xid_error)}",
                )
            )

        if e.temp_c >= 87:  # GB10/H100 slowdown territory
            out.append(
                Anomaly(
                    job=e.job, node=e.node, gpu=e.gpu, metric="temp_c",
                    value=e.temp_c, baseline=70.0, zscore=(e.temp_c - 70) / 5,
                    severity=Severity.warning, kind="thermal",
                    message=f"{e.temp_c:.0f}C on {e.node}/gpu{e.gpu} - thermal throttling, check cooling",
                )
            )
        return out

    # -- layer 2b: memory / OOM ----------------------------------------------
    def _memory_rules(self, e: TelemetryEvent) -> list[Anomaly]:
        if e.mem_pct >= 98:
            return [
                Anomaly(
                    job=e.job, node=e.node, gpu=e.gpu, metric="mem_pct",
                    value=e.mem_pct, baseline=80.0, zscore=(e.mem_pct - 80) / 5,
                    severity=Severity.critical, kind="oom",
                    message=f"HBM at {e.mem_pct:.0f}% on {e.node}/gpu{e.gpu} - OOM imminent",
                )
            ]
        return []

    # -- layer 2c: straggler detection (cross-rank) --------------------------
    def _straggler(self, e: TelemetryEvent) -> Optional[Anomaly]:
        if e.step_time_ms is None:
            return None
        self._rank_step[e.job][(e.node, e.gpu)] = e.step_time_ms
        peers = list(self._rank_step[e.job].values())
        if len(peers) < 4:
            return None
        median = statistics.median(peers)
        if median <= 0:
            return None
        ratio = e.step_time_ms / median
        # A rank >50% slower than the cluster median stalls every all-reduce.
        if ratio >= 1.5:
            return Anomaly(
                job=e.job, node=e.node, gpu=e.gpu, metric="step_time_ms",
                value=e.step_time_ms, baseline=median, zscore=ratio,
                severity=Severity.critical, kind="straggler",
                message=f"{e.node}/gpu{e.gpu} is {ratio:.1f}x the cluster-median step time "
                        f"({e.step_time_ms:.0f}ms vs {median:.0f}ms) - stalling every collective",
            )
        return None


def _metric_kind(metric: str) -> str:
    return {
        "step_time_ms": "slowdown",
        "nccl_allreduce_ms": "collective_stall",
        "temp_c": "thermal",
        "tokens_per_s": "throughput_drop",
        "fabric_rx_gbps": "fabric",
    }.get(metric, "anomaly")


def _describe(metric: str, value: float, baseline: float, z: float) -> str:
    pct = 100 * (value - baseline) / baseline if baseline else 0
    labels = {
        "step_time_ms": "step time",
        "nccl_allreduce_ms": "all-reduce latency",
        "temp_c": "temperature",
        "tokens_per_s": "throughput",
        "fabric_rx_gbps": "fabric RX bandwidth",
    }
    return f"{labels.get(metric, metric)} at {value:.1f} ({pct:+.0f}% vs baseline, z={z:.1f})"


def _xid_hint(code: int) -> str:
    return {
        13: "graphics engine exception",
        31: "GPU memory page fault (often a bad user kernel or failing HBM)",
        48: "double-bit ECC error - uncorrectable",
        63: "row-remapping event pending, reset required",
        79: "GPU has fallen off the bus - hardware fault",
        94: "contained ECC error",
    }.get(code, "consult the XID table; reset/drain the GPU")
