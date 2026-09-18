import asyncio

import pytest

from gpumon import remediation
from gpumon.models import Anomaly, Incident, RemediationRecord, Severity
from gpumon.workflow import IncidentManager


def anomaly(kind="straggler", node="gb10-node-02", gpu=5):
    return Anomaly(
        job="rem-job", node=node, gpu=gpu, metric="step_time_ms",
        value=520, baseline=180, zscore=2.9, severity=Severity.critical,
        kind=kind, message=f"{kind} on {node}/gpu{gpu}",
    )


def incident_with(kind):
    return Incident(job="rem-job", title="t", severity=Severity.critical, anomalies=[anomaly(kind)])


def test_proposal_maps_kind_to_playbook():
    p = remediation.propose(incident_with("straggler"))
    assert p.action == "cordon_gpu_and_restart"
    assert p.requires_approval is True  # medium risk
    assert p.target == "gb10-node-02/gpu5"
    assert p.steps


def test_low_risk_action_needs_no_approval():
    p = remediation.propose(incident_with("oom"))
    assert p.risk == "low"
    assert p.requires_approval is False


def test_hardware_faults_take_priority_in_proposal():
    inc = Incident(
        job="rem-job", title="t", severity=Severity.critical,
        anomalies=[anomaly("straggler"), anomaly("ecc", gpu=1)],
    )
    assert remediation.propose(inc).action == "drain_node"


def test_execute_writes_full_audit_trail():
    rec = RemediationRecord(proposal=remediation.propose(incident_with("straggler")))
    remediation.execute(rec, approved_by="alice")
    assert rec.status == "COMPLETED"
    assert rec.approved_by == "alice"
    kinds = [e["kind"] for e in rec.audit]
    assert kinds[0] == "approved" and kinds[-1] == "completed"
    assert kinds.count("step") == len(rec.proposal.steps)


@pytest.mark.asyncio
async def test_manager_remediation_resolves_and_actuates():
    events, actuated = [], []

    async def broadcaster(m):
        events.append(m["type"])

    async def actuator(proposal):
        actuated.append(proposal.target)

    mgr = IncidentManager(broadcaster=broadcaster, actuator=actuator)
    await mgr.ingest_anomalies("rem-job", [anomaly("straggler")])
    for _ in range(50):
        await asyncio.sleep(0.01)
        if mgr.open_incidents and mgr.open_incidents[0].remediation:
            break

    inc_id = mgr.open_incidents[0].id
    resolved = await mgr.remediate(inc_id, approved_by="operator")
    assert resolved.status == "RESOLVED"
    assert resolved.remediation.status == "COMPLETED"
    assert actuated == ["gb10-node-02/gpu5"]
    assert "remediation_started" in events and "remediation_completed" in events
    assert mgr.open_incidents == []
