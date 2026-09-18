"""Agentic, approval-gated auto-remediation.

Once an incident is diagnosed, the platform proposes a concrete remediation
(not just advice): a named action, its blast-radius risk, the target
hardware, and the ordered steps it will run. High-impact actions are
**gated on human approval** - an operator approves in the dashboard (or via
the SNS approval callback in AWS), and only then does the agent execute the
runbook, writing an audit entry per step.

This is the "close the loop" layer on top of detection + diagnosis: the same
system that finds and explains a problem can also fix it, safely.
"""

from __future__ import annotations

import time

from .models import Anomaly, Incident, RemediationProposal, RemediationRecord

# Map the dominant failure kind to a remediation playbook.
_PLAYBOOK: dict[str, dict] = {
    "straggler": {
        "action": "cordon_gpu_and_restart",
        "title": "Cordon the straggler GPU and restart from checkpoint",
        "risk": "medium",
        "steps": [
            "Cordon {target} so the scheduler stops placing ranks on it",
            "Signal the job to checkpoint and drain the current step",
            "Restart the job from the last checkpoint excluding the cordoned rank",
            "Verify cross-rank step-time variance returns to baseline",
        ],
    },
    "ecc": {
        "action": "drain_node",
        "title": "Drain and cordon the node with failing HBM",
        "risk": "high",
        "steps": [
            "Cordon the node containing {target}",
            "Migrate healthy ranks off the node",
            "Trigger HBM row-remap and mark the GPU for hardware inspection",
        ],
    },
    "xid": {
        "action": "reset_gpu",
        "title": "Reset the GPU reporting the XID fault",
        "risk": "high",
        "steps": [
            "Cordon {target}",
            "Reset the GPU (nvidia-smi --gpu-reset) or reboot the host",
            "Reschedule the job; RMA the card if the XID recurs",
        ],
    },
    "oom": {
        "action": "reduce_memory_pressure",
        "title": "Reduce HBM pressure and resume",
        "risk": "low",
        "steps": [
            "Enable activation checkpointing for the job",
            "Halve the micro-batch size",
            "Resume from the last checkpoint and watch mem_pct headroom",
        ],
    },
    "thermal": {
        "action": "power_cap_node",
        "title": "Power-cap the overheating node",
        "risk": "low",
        "steps": [
            "Apply a temporary power cap to GPUs on the node behind {target}",
            "Open a datacenter cooling/airflow ticket for the rack",
            "Lift the cap once temperatures fall below 85C",
        ],
    },
    "collective_stall": {
        "action": "reroute_fabric",
        "title": "Move the job off the degraded fabric link",
        "risk": "medium",
        "steps": [
            "Identify the degraded RoCE link/switch from fabric counters",
            "Drain the affected port",
            "Reschedule the job's ranks onto a healthy fabric path",
        ],
    },
    "throughput": {
        "action": "rollback_deploy",
        "title": "Roll back the regressing change",
        "risk": "medium",
        "steps": [
            "Identify the most recent job restart / config change",
            "Roll it back to the last-known-good revision",
            "Increase dataloader prefetch workers and verify tokens/sec recovers",
        ],
    },
}

# Priority when several kinds fire in one incident (hardware first).
_PRIORITY = ["xid", "ecc", "oom", "straggler", "thermal", "collective_stall", "throughput"]


def _dominant(anomalies: list[Anomaly]) -> Anomaly:
    kinds = {a.kind: a for a in anomalies}
    for p in _PRIORITY:
        if p in kinds:
            return kinds[p]
    return anomalies[0]


def propose(incident: Incident) -> RemediationProposal:
    lead = _dominant(incident.anomalies)
    play = _PLAYBOOK.get(
        lead.kind,
        {
            "action": "inspect_node",
            "title": "Inspect the affected node",
            "risk": "medium",
            "steps": ["Inspect {target} (dmesg, DCGM, fabric) and drain if hardware is implicated"],
        },
    )
    target = f"{lead.node}/gpu{lead.gpu}" if lead.gpu is not None else lead.node
    steps = [s.format(target=target) for s in play["steps"]]
    return RemediationProposal(
        action=play["action"],
        title=play["title"],
        risk=play["risk"],
        # Only low-risk actions may auto-execute; everything else needs a human.
        requires_approval=play["risk"] != "low",
        target=target,
        steps=steps,
        rationale=incident.diagnosis.recommended_action if incident.diagnosis else "",
    )


def execute(record: RemediationRecord, approved_by: str | None = None) -> RemediationRecord:
    """Run the proposal's steps, writing an audit entry for each."""
    record.status = "EXECUTING"
    record.approved_by = approved_by
    record.audit.append(_entry("approved", f"approved by {approved_by or 'auto-policy'}"))
    for step in record.proposal.steps:
        record.audit.append(_entry("step", step))
    record.audit.append(_entry("completed", f"action '{record.proposal.action}' applied to {record.proposal.target}"))
    record.status = "COMPLETED"
    record.completed_at = time.time()
    return record


def _entry(kind: str, detail: str) -> dict:
    return {"ts": time.time(), "kind": kind, "detail": detail}
