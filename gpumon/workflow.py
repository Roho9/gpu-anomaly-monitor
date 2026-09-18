"""Incident lifecycle: correlate anomalies -> open -> diagnose -> alert -> resolve.

This mirrors the Step Functions state machine in the AWS deployment. Each
method here maps to a state:

    Correlate -> OpenIncident -> Diagnose (Bedrock) -> Alert (SNS) -> [Resolve]

Correlation is the important bit: raw detectors fire on every sample, but an
operator wants *one* incident per problem. We group firing anomalies per job
into a single open incident and keep enriching it until the job goes quiet.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Awaitable, Callable, Optional

from . import remediation
from .models import Anomaly, Incident, RemediationProposal, RemediationRecord, Severity
from .rca.engine import diagnose_incident
from .rca.rag import kb
from .store import store

# Async callback the API layer registers to push updates to the dashboard and
# to fan out alerts (the SNS equivalent).
Broadcaster = Callable[[dict], Awaitable[None]]
# Actuator performs the real-world remediation (clears the fault). In the demo
# it cordons/clears in the simulator; in AWS it calls the scheduler/fabric APIs.
Actuator = Callable[[RemediationProposal], Awaitable[None]]

RESOLVE_COOLDOWN_S = 20.0  # a job quiet this long auto-resolves its incident


def _title(job: str, anomalies: list[Anomaly]) -> str:
    kinds = {a.kind for a in anomalies}
    lead = max(anomalies, key=lambda a: abs(a.zscore))
    label = {
        "straggler": "Straggler stalling collectives",
        "ecc": "ECC errors / failing HBM",
        "xid": "GPU XID hardware fault",
        "oom": "HBM out-of-memory",
        "thermal": "Thermal throttling",
        "collective_stall": "NCCL all-reduce fabric stall",
        "throughput_drop": "Training throughput collapse",
        "slowdown": "Step-time regression",
        "fabric": "RoCE fabric degradation",
    }.get(lead.kind, "Cluster anomaly")
    scope = f"{lead.node}/gpu{lead.gpu}" if lead.gpu is not None else lead.node
    extra = f" (+{len(kinds) - 1} more signal types)" if len(kinds) > 1 else ""
    return f"[{job}] {label} on {scope}{extra}"


class IncidentManager:
    def __init__(
        self,
        broadcaster: Optional[Broadcaster] = None,
        actuator: Optional[Actuator] = None,
    ) -> None:
        self._open: dict[str, Incident] = {}          # job -> open incident
        self._last_anomaly: dict[str, float] = {}     # job -> ts
        self._broadcaster = broadcaster
        self._actuator = actuator
        self._diagnosing: set[str] = set()

    def set_broadcaster(self, broadcaster: Broadcaster) -> None:
        self._broadcaster = broadcaster

    def set_actuator(self, actuator: Actuator) -> None:
        self._actuator = actuator

    @property
    def open_incidents(self) -> list[Incident]:
        return list(self._open.values())

    async def _emit(self, kind: str, payload: dict) -> None:
        if self._broadcaster:
            await self._broadcaster({"type": kind, **payload})

    async def ingest_anomalies(self, job: str, anomalies: list[Anomaly]) -> None:
        if not anomalies:
            return
        self._last_anomaly[job] = time.time()
        incident = self._open.get(job)

        if incident is None:
            incident = Incident(
                job=job,
                title=_title(job, anomalies),
                severity=_max_sev(anomalies),
                status="OPEN",
                anomalies=list(anomalies),
            )
            self._open[job] = incident
            store.put_incident(incident)
            await self._emit("incident_opened", {"incident": incident.model_dump()})
            asyncio.create_task(self._diagnose(incident))
        else:
            # Enrich the existing incident, deduping by (kind, node, gpu).
            seen = {(a.kind, a.node, a.gpu) for a in incident.anomalies}
            new = [a for a in anomalies if (a.kind, a.node, a.gpu) not in seen]
            if new:
                incident.anomalies.extend(new)
                incident.severity = _max_sev(incident.anomalies)
                incident.title = _title(job, incident.anomalies)
                store.put_incident(incident)
                await self._emit("incident_updated", {"incident": incident.model_dump()})

    async def _diagnose(self, incident: Incident) -> None:
        if incident.id in self._diagnosing:
            return
        self._diagnosing.add(incident.id)
        try:
            incident.status = "DIAGNOSING"
            await self._emit("incident_updated", {"incident": incident.model_dump()})
            # RCA may block (Bedrock call) - keep the event loop free.
            diagnosis = await asyncio.to_thread(diagnose_incident, incident)
            incident.diagnosis = diagnosis
            incident.status = "DIAGNOSED"
            # Propose a concrete, approval-gated remediation alongside the RCA.
            incident.remediation = RemediationRecord(proposal=remediation.propose(incident))
            store.put_incident(incident)
            await self._emit("incident_diagnosed", {"incident": incident.model_dump()})
            await self._alert(incident)
            # Low-risk actions may auto-remediate when the policy allows it.
            if (
                not incident.remediation.proposal.requires_approval
                and os.environ.get("ARGUS_AUTO_REMEDIATE") == "1"
            ):
                await self._run_remediation(incident, approved_by=None)
        finally:
            self._diagnosing.discard(incident.id)

    async def remediate(self, incident_id: str, approved_by: str) -> Optional[Incident]:
        """Approve and execute the proposed remediation for an incident."""
        incident = next((i for i in self._open.values() if i.id == incident_id), None)
        if incident is None:
            incident = store.get_incident(incident_id)
        if incident is None or incident.remediation is None:
            return None
        await self._run_remediation(incident, approved_by=approved_by)
        return incident

    async def _run_remediation(self, incident: Incident, approved_by: Optional[str]) -> None:
        record = incident.remediation
        if record is None or record.status in {"EXECUTING", "COMPLETED"}:
            return
        incident.status = "REMEDIATING"
        await self._emit("remediation_started", {"incident": incident.model_dump()})
        # Execute the audited runbook, then actuate the real-world change.
        remediation.execute(record, approved_by=approved_by)
        if self._actuator is not None:
            await self._actuator(record.proposal)
        incident.status = "RESOLVED"
        incident.resolved_at = time.time()
        store.put_incident(incident)
        self._index_for_rag(incident)
        self._open.pop(incident.job, None)
        await self._emit("remediation_completed", {"incident": incident.model_dump()})
        await self._emit("incident_resolved", {"incident": incident.model_dump()})

    async def _alert(self, incident: Incident) -> None:
        """The SNS-equivalent fan-out (email/SMS/Slack in AWS mode)."""
        d = incident.diagnosis
        await self._emit(
            "alert",
            {
                "incident_id": incident.id,
                "severity": incident.severity.value,
                "title": incident.title,
                "root_cause": d.root_cause if d else None,
                "action": d.recommended_action if d else None,
            },
        )

    async def sweep(self) -> None:
        """Auto-resolve jobs that have been quiet past the cooldown."""
        now = time.time()
        for job, incident in list(self._open.items()):
            last = self._last_anomaly.get(job, incident.opened_at)
            if now - last >= RESOLVE_COOLDOWN_S and incident.status == "DIAGNOSED":
                incident.status = "RESOLVED"
                incident.resolved_at = now
                store.put_incident(incident)
                # Feed the resolved incident back into the RAG corpus so future
                # incidents can retrieve it as a similar past case.
                self._index_for_rag(incident)
                del self._open[job]
                await self._emit("incident_resolved", {"incident": incident.model_dump()})

    @staticmethod
    def _index_for_rag(incident: Incident) -> None:
        d = incident.diagnosis
        text = (
            f"{incident.title}\nSignals:\n{incident.summary_signals()}\n"
            + (f"Root cause: {d.root_cause}\nAction: {d.recommended_action}" if d else "")
        )
        kb.index_incident(incident.id, incident.title, text)


def _max_sev(anomalies: list[Anomaly]) -> Severity:
    order = {Severity.info: 0, Severity.warning: 1, Severity.critical: 2}
    return max((a.severity for a in anomalies), key=lambda s: order[s])
