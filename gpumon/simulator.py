"""Synthetic GPU/ML training-cluster telemetry with injectable failures.

Generates a fleet of nodes x GPUs running a distributed job at a steady
baseline, then injects realistic failure scenarios on a timeline so the
detection -> incident -> RCA -> alert -> resolve pipeline can be demonstrated
end to end. The scenarios are the ones that actually take down large training
runs, not generic HTTP 500s.
"""

from __future__ import annotations

import random

from .models import TelemetryEvent

# name -> (human description, duration in steps)
SCENARIOS = {
    "straggler": "One rank falls behind and stalls every all-reduce",
    "ecc": "Accumulating ECC errors on a GPU (failing HBM)",
    "xid": "NVIDIA XID hardware fault on a GPU",
    "oom": "HBM out-of-memory on a rank",
    "thermal": "A node overheats and throttles",
    "nccl": "Cluster-wide NCCL/RoCE fabric stall",
    "throughput": "Input-bound throughput collapse",
}


class ClusterSimulator:
    def __init__(
        self,
        job: str = "llama3-70b-sft",
        nodes: int = 4,
        gpus_per_node: int = 8,
        base_step_ms: float = 180.0,
        seed: int | None = 7,
    ) -> None:
        self.job = job
        self.rng = random.Random(seed)
        self.base_step_ms = base_step_ms
        self.step = 0
        self.fleet: list[tuple[str, int, int]] = []
        rank = 0
        for n in range(nodes):
            node = f"gb10-node-{n:02d}"
            for g in range(gpus_per_node):
                self.fleet.append((node, g, rank))
                rank += 1
        # Active fault: (scenario, target_node, target_gpu, steps_remaining)
        self._fault: tuple[str, str, int, int] | None = None

    # -- scenario control ----------------------------------------------------
    def inject(self, scenario: str, steps: int = 15, node: str | None = None, gpu: int | None = None) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}")
        node = node or self.rng.choice([n for n, _, _ in self.fleet])
        gpu = gpu if gpu is not None else self.rng.randrange(8)
        self._fault = (scenario, node, gpu, steps)

    @property
    def active_fault(self) -> str | None:
        return self._fault[0] if self._fault else None

    # -- generation ----------------------------------------------------------
    def tick(self) -> list[TelemetryEvent]:
        self.step += 1
        events = [self._event(node, gpu, rank) for node, gpu, rank in self.fleet]
        if self._fault:
            scen, node, gpu, remaining = self._fault
            self._fault = (scen, node, gpu, remaining - 1) if remaining > 1 else None
        return events

    def _event(self, node: str, gpu: int, rank: int) -> TelemetryEvent:
        r = self.rng
        step_ms = self.base_step_ms * r.uniform(0.96, 1.04)
        tokens = 3200 * r.uniform(0.97, 1.03)
        e = TelemetryEvent(
            job=self.job, node=node, gpu=gpu, rank=rank,
            sm_util=r.uniform(88, 98),
            mem_used_gb=r.uniform(58, 64), mem_total_gb=80.0,
            temp_c=r.uniform(64, 74),
            power_w=r.uniform(620, 690),
            ecc_errors=0,
            step=self.step, step_time_ms=step_ms, tokens_per_s=tokens,
            nccl_allreduce_ms=r.uniform(11, 15),
            fabric_rx_gbps=r.uniform(180, 195),
        )
        return self._apply_fault(e)

    def _apply_fault(self, e: TelemetryEvent) -> TelemetryEvent:
        if not self._fault:
            return e
        scen, node, gpu, _ = self._fault
        target = e.node == node and e.gpu == gpu
        node_wide = e.node == node
        r = self.rng

        if scen == "straggler" and target:
            e.step_time_ms *= r.uniform(2.1, 2.6)
            e.sm_util = r.uniform(55, 70)
        elif scen == "ecc" and target:
            e.ecc_errors = r.randint(1, 3)
        elif scen == "xid" and target:
            e.xid_error = r.choice([48, 63, 79])
        elif scen == "oom" and target:
            e.mem_used_gb = r.uniform(78.6, 79.8)
        elif scen == "thermal" and node_wide:
            e.temp_c = r.uniform(88, 93)
            e.step_time_ms *= 1.15
        elif scen == "nccl":  # fabric-wide
            e.nccl_allreduce_ms *= r.uniform(5, 8)
            e.step_time_ms *= 1.4
            e.fabric_rx_gbps *= 0.4
        elif scen == "throughput":  # job-wide input-bound collapse
            e.tokens_per_s *= 0.55
            e.step_time_ms *= 1.8
            e.sm_util = r.uniform(30, 45)
        return e


# A demo timeline: (start_step, scenario, duration). Warm-up first so the
# statistical baselines are established before the first fault.
DEMO_TIMELINE = [
    (25, "straggler", 14),
    (60, "ecc", 12),
    (95, "nccl", 12),
    (130, "oom", 10),
    (160, "thermal", 12),
    (195, "throughput", 14),
    (230, "xid", 8),
]
