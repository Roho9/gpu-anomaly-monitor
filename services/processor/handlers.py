"""AWS Lambda handlers for the deployed pipeline.

These are thin adapters: all the real logic lives in the ``gpumon`` package
so the local runtime and the deployed functions share one implementation.
Each handler maps to a stage in the architecture diagram.

Note: the stream processor keeps anomaly-detection baselines in a module-level
engine (warm-container state). In production those baselines are checkpointed
to DynamoDB/ElastiCache so they survive cold starts and shard rebalancing;
that persistence is elided here to keep the example readable.
"""

from __future__ import annotations

import base64
import json
import os

import boto3

from gpumon import remediation
from gpumon.detectors import DetectorEngine
from gpumon.models import Anomaly, Incident, RemediationProposal, RemediationRecord, TelemetryEvent, Severity
from gpumon.rca import bedrock
from gpumon.rca.rag import kb

_detector = DetectorEngine()  # persists across warm invocations


# --- POST /events -> Kinesis --------------------------------------------------
def ingest(event, _context):
    body = json.loads(event.get("body") or "[]")
    batch = body if isinstance(body, list) else [body]
    client = boto3.client("kinesis")
    records = [
        {"Data": json.dumps(e).encode(), "PartitionKey": e.get("node", "unknown")}
        for e in batch
    ]
    if records:
        client.put_records(StreamName=os.environ["ARGUS_STREAM"], Records=records)
    return {"statusCode": 202, "body": json.dumps({"accepted": len(records)})}


# --- Kinesis -> anomaly detection -> Step Functions ---------------------------
def process_batch(event, _context):
    started = 0
    by_job: dict[str, list[Anomaly]] = {}
    for record in event["Records"]:
        payload = json.loads(base64.b64decode(record["kinesis"]["data"]))
        te = TelemetryEvent(**payload)
        for anom in _detector.process(te):
            by_job.setdefault(te.job, []).append(anom)

    sfn = boto3.client("stepfunctions")
    for job, anomalies in by_job.items():
        execution_input = {
            "job": job,
            "anomalies": [a.model_dump() for a in anomalies],
        }
        sfn.start_execution(
            stateMachineArn=os.environ["WORKFLOW_ARN"],
            input=json.dumps(execution_input, default=str),
        )
        started += 1
    return {"incidentsStarted": started}


# --- Step Functions state 1: gather RAG context -------------------------------
def gather_context(event, _context):
    anomalies = [Anomaly(**a) for a in event["anomalies"]]
    incident = Incident(
        job=event["job"],
        title=f"[{event['job']}] {anomalies[0].kind} incident",
        severity=_max_sev(anomalies),
        anomalies=anomalies,
    )
    signals = incident.summary_signals()
    runbooks, past = kb.retrieve(signals, k=3)
    return {
        "incident": incident.model_dump(),
        "signals": signals,
        "runbook_ids": [d.doc_id for d, _ in runbooks],
        "runbook_text": [d.text for d, _ in runbooks],
    }


# --- Step Functions state 2: Bedrock diagnosis --------------------------------
def diagnose(event, _context):
    from gpumon.rca.rag import Document

    runbooks = [
        (Document(rid, "runbook", rid, txt), 1.0)
        for rid, txt in zip(event["runbook_ids"], event["runbook_text"])
    ]
    d = bedrock.diagnose(event["signals"], runbooks, [])
    incident = event["incident"]
    incident["diagnosis"] = d.model_dump()
    incident["status"] = "DIAGNOSED"
    return {
        "incident": incident,
        "alert": {
            "title": incident["title"],
            "severity": incident["severity"],
            "root_cause": d.root_cause,
            "action": d.recommended_action,
        },
    }


# --- Step Functions state 3: persist to DynamoDB ------------------------------
def persist_incident(event, _context):
    incident = event["incident"]
    table = boto3.resource("dynamodb").Table(os.environ["ARGUS_DDB_TABLE"])
    table.put_item(
        Item={
            "pk": f"JOB#{incident['job']}",
            "sk": f"INCIDENT#{incident['opened_at']}#{incident['id']}",
            "status": incident["status"],
            "doc": json.dumps(incident, default=str),
        }
    )
    return event


# --- Remediation workflow: apply the approved action -------------------------
def apply_remediation(event, _context):
    """Execute an approved remediation and mark the incident resolved.

    Invoked by the remediation Step Functions workflow after the approval gate
    (or directly for low-risk auto-remediation).
    """
    record = RemediationRecord(proposal=RemediationProposal(**event["proposal"]))
    remediation.execute(record, approved_by=event.get("approved_by", "auto-policy"))

    incident = event["incident"]
    incident["remediation"] = record.model_dump()
    incident["status"] = "RESOLVED"
    table = boto3.resource("dynamodb").Table(os.environ["ARGUS_DDB_TABLE"])
    table.put_item(
        Item={
            "pk": f"JOB#{incident['job']}",
            "sk": f"INCIDENT#{incident['opened_at']}#{incident['id']}",
            "status": "RESOLVED",
            "doc": json.dumps(incident, default=str),
        }
    )
    return {"incident": incident, "audit": record.audit}


def _max_sev(anomalies: list[Anomaly]) -> Severity:
    order = {Severity.info: 0, Severity.warning: 1, Severity.critical: 2}
    return max((a.severity for a in anomalies), key=lambda s: order[s])
