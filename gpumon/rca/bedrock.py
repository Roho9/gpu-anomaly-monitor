"""LLM adapter.

`diagnose()` builds a grounded prompt and returns structured JSON. Two
backends implement the same contract:

* **Bedrock** (AWS mode) - calls Claude on Amazon Bedrock via the Messages
  API and parses the JSON the model returns.
* **Offline heuristic** (local/CI) - a deterministic diagnoser that reasons
  from the retrieved runbook + anomaly features. It is intentionally good
  enough to make the end-to-end demo meaningful without any credentials, and
  it exercises the identical prompt-assembly and parsing path.

Selection is by config, so switching a running deployment to real Bedrock is
a single env var.
"""

from __future__ import annotations

import json

from ..config import settings
from ..models import Diagnosis

SYSTEM_PROMPT = """You are the root-cause analysis engine for a GPU/ML training \
cluster monitor. You are given the firing anomalies for one incident, plus \
retrieved runbooks and similar past incidents. Diagnose the most likely root \
cause of the training-cluster problem. Be specific to distributed-training \
failure modes (stragglers, NCCL/RoCE fabric stalls, ECC/XID hardware faults, \
HBM OOM, thermal throttling, bad checkpoints/deploys). Respond with ONLY a \
JSON object: {"root_cause": str, "confidence": 0..1, "reasoning": str, \
"recommended_action": str}."""


def build_prompt(signals: str, runbooks: list, incidents: list) -> str:
    parts = ["## Firing anomalies\n" + signals]
    if runbooks:
        parts.append(
            "## Relevant runbooks\n"
            + "\n\n".join(f"### {d.title}\n{d.text[:1200]}" for d, _ in runbooks)
        )
    if incidents:
        parts.append(
            "## Similar past incidents\n"
            + "\n\n".join(f"### {d.title}\n{d.text[:600]}" for d, _ in incidents)
        )
    return "\n\n".join(parts)


def diagnose(signals: str, runbooks: list, incidents: list) -> Diagnosis:
    runbook_ids = [d.doc_id for d, _ in runbooks]
    incident_ids = [d.doc_id for d, _ in incidents]
    prompt = build_prompt(signals, runbooks, incidents)

    if settings.use_bedrock:
        raw = _call_bedrock(prompt)  # pragma: no cover - needs AWS
        model = settings.bedrock_model
    else:
        raw = _offline_diagnose(signals, runbooks)
        model = "offline-heuristic"

    data = _parse(raw)
    return Diagnosis(
        root_cause=data["root_cause"],
        confidence=float(data.get("confidence", 0.6)),
        reasoning=data["reasoning"],
        recommended_action=data["recommended_action"],
        similar_incidents=incident_ids,
        runbooks_used=runbook_ids,
        model=model,
    )


def _parse(raw: str) -> dict:
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return {
            "root_cause": "Unstructured model output",
            "confidence": 0.3,
            "reasoning": raw[:500],
            "recommended_action": "Review the incident signals manually.",
        }


def _call_bedrock(prompt: str) -> str:  # pragma: no cover - needs AWS
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    resp = client.invoke_model(modelId=settings.bedrock_model, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    return payload["content"][0]["text"]


def _offline_diagnose(signals: str, runbooks: list) -> str:
    """Deterministic RCA used when Bedrock is not available.

    Maps the dominant anomaly kind to a grounded diagnosis, and pulls the
    recommended action from the top retrieved runbook when present.
    """
    kinds = [line.split("]")[0].lstrip("- [") for line in signals.splitlines() if line.startswith("- [")]
    top = _dominant(kinds)
    runbook_title = runbooks[0][0].title if runbooks else None

    table = {
        "straggler": (
            "A single rank is running well behind its peers, so every all-reduce "
            "blocks on it and cluster throughput collapses to the slowest GPU.",
            0.82,
            "Isolate the slow rank (checked in the signals), cordon that GPU/node, "
            "and restart the job from the last checkpoint excluding it.",
        ),
        "ecc": (
            "Accumulating ECC errors indicate failing HBM on the affected GPU; "
            "uncorrectable errors will crash the job and corrupt gradients.",
            0.88,
            "Drain and cordon the node, run field diagnostics / row-remap, and "
            "replace the GPU before returning it to the pool.",
        ),
        "xid": (
            "An XID fault is a hardware/driver-level GPU error; the device is "
            "unreliable for the rest of the run.",
            0.9,
            "Reset the GPU (or reboot the node), and if it recurs, RMA the card.",
        ),
        "oom": (
            "HBM is saturated - likely a batch-size / sequence-length increase, a "
            "memory leak in the training loop, or activation checkpointing disabled.",
            0.75,
            "Reduce micro-batch size or enable activation checkpointing; compare "
            "against the config of the last healthy run.",
        ),
        "thermal": (
            "GPUs are hitting thermal limits and clock-throttling, which shows up "
            "as a uniform slowdown across the affected node.",
            0.7,
            "Check datacenter cooling / airflow for the rack and cap power if the "
            "thermal issue cannot be resolved immediately.",
        ),
        "collective_stall": (
            "All-reduce latency has spiked cluster-wide, pointing at the RoCE/IB "
            "fabric rather than any single GPU - a flapping link, congestion, or a "
            "degraded switch.",
            0.72,
            "Inspect fabric counters and ECN/PFC on the top-of-rack switch; move "
            "the job off the degraded link.",
        ),
        "throughput_drop": (
            "Tokens/sec fell sharply without a matching compute-utilisation drop, "
            "which usually means the pipeline is now input-bound (dataloader / "
            "storage) or a recent code/deploy change regressed the step.",
            0.6,
            "Correlate with the most recent job restart or config change and roll "
            "it back; check dataloader worker count and storage throughput.",
        ),
    }
    root, conf, action = table.get(
        top,
        (
            "Multiple correlated anomalies without a single dominant cause; "
            "likely a node-level event affecting several GPUs at once.",
            0.5,
            "Inspect the affected node holistically (dmesg, DCGM, fabric) and "
            "drain it if hardware is implicated.",
        ),
    )
    reasoning = (
        f"Dominant signal is '{top}'. " + root
        + (f" Grounded in runbook: '{runbook_title}'." if runbook_title else "")
    )
    return json.dumps(
        {
            "root_cause": root,
            "confidence": conf,
            "reasoning": reasoning,
            "recommended_action": action,
        }
    )


def _dominant(kinds: list[str]) -> str:
    if not kinds:
        return "unknown"
    # Hardware faults win over derived signals when both are present.
    priority = ["xid", "ecc", "oom", "straggler", "thermal", "collective_stall", "throughput_drop"]
    for p in priority:
        if p in kinds:
            return p
    return kinds[0]
