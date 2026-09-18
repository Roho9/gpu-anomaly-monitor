from gpumon.rca import bedrock
from gpumon.rca.rag import kb


def test_retrieval_finds_relevant_runbook():
    runbooks, _ = kb.retrieve("one rank is a straggler stalling every all-reduce collective", k=3)
    assert runbooks, "expected at least one runbook match"
    assert runbooks[0][0].doc_id == "straggler"


def test_offline_diagnosis_is_grounded_and_structured():
    signals = "- [straggler] gb10-node-01/gpu5: step_time_ms=520 (baseline 180, z=2.9) -> slow rank"
    runbooks, incidents = kb.retrieve(signals, k=3)
    d = bedrock.diagnose(signals, runbooks, incidents)
    assert d.model == "offline-heuristic"
    assert 0 < d.confidence <= 1
    assert d.recommended_action
    assert "rank" in d.root_cause.lower()
    assert d.runbooks_used  # grounded in retrieved runbooks


def test_hardware_faults_take_priority():
    signals = (
        "- [straggler] n/gpu0: step_time_ms=500 -> slow\n"
        "- [ecc] n/gpu0: ecc_errors=6 -> failing HBM"
    )
    d = bedrock.diagnose(signals, *kb.retrieve(signals, k=3))
    assert "hbm" in d.root_cause.lower() or "ecc" in d.reasoning.lower()


def test_prompt_includes_context():
    runbooks, incidents = kb.retrieve("oom hbm memory", k=2)
    prompt = bedrock.build_prompt("- [oom] n/gpu0: mem_pct=99", runbooks, incidents)
    assert "Firing anomalies" in prompt
    assert "runbook" in prompt.lower()
