import asyncio

import pytest

from gpumon.models import Anomaly, Severity
from gpumon.workflow import IncidentManager


def straggler_anomaly(job="wf-job"):
    return Anomaly(
        job=job, node="gb10-node-02", gpu=5, metric="step_time_ms",
        value=520, baseline=180, zscore=2.9, severity=Severity.critical,
        kind="straggler", message="rank is 2.9x the cluster median step time",
    )


@pytest.mark.asyncio
async def test_incident_opens_diagnoses_and_alerts():
    events = []

    async def broadcaster(msg):
        events.append(msg)

    mgr = IncidentManager(broadcaster=broadcaster)
    await mgr.ingest_anomalies("wf-job", [straggler_anomaly()])

    # let the scheduled diagnosis task (offline, fast) complete
    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(e["type"] == "alert" for e in events):
            break

    types = [e["type"] for e in events]
    assert "incident_opened" in types
    assert "incident_diagnosed" in types
    assert "alert" in types

    inc = mgr.open_incidents[0]
    assert inc.status == "DIAGNOSED"
    assert inc.diagnosis is not None
    assert inc.diagnosis.recommended_action


@pytest.mark.asyncio
async def test_duplicate_anomalies_do_not_open_second_incident():
    mgr = IncidentManager(broadcaster=lambda m: asyncio.sleep(0))
    await mgr.ingest_anomalies("dedupe-job", [straggler_anomaly("dedupe-job")])
    await mgr.ingest_anomalies("dedupe-job", [straggler_anomaly("dedupe-job")])
    assert len(mgr.open_incidents) == 1
    assert len(mgr.open_incidents[0].anomalies) == 1
