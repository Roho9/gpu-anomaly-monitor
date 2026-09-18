"""RCA orchestration: incident signals -> retrieval -> grounded diagnosis."""

from __future__ import annotations

from ..models import Diagnosis, Incident
from . import bedrock
from .rag import kb


def diagnose_incident(incident: Incident) -> Diagnosis:
    signals = incident.summary_signals()
    # Query the knowledge base with the incident title + its firing signals.
    query = f"{incident.title}\n{signals}"
    runbooks, incidents = kb.retrieve(query, k=3)
    return bedrock.diagnose(signals, runbooks, incidents)
