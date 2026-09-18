import random

from gpumon.detectors import DetectorEngine
from helpers import make_event


def kinds(anoms):
    return {a.kind for a in anoms}


def test_statistical_slowdown_fires_after_baseline():
    eng = DetectorEngine()
    rng = random.Random(1)
    # warm up a stable baseline
    for _ in range(15):
        eng.process(make_event(step_time_ms=180 + rng.uniform(-3, 3)))
    fired = eng.process(make_event(step_time_ms=600))
    assert "slowdown" in kinds(fired)
    a = next(a for a in fired if a.kind == "slowdown")
    assert a.zscore >= 3


def test_throughput_drop_is_low_direction():
    eng = DetectorEngine()
    rng = random.Random(2)
    for _ in range(15):
        eng.process(make_event(tokens_per_s=3200 + rng.uniform(-40, 40)))
    fired = eng.process(make_event(tokens_per_s=1400))
    assert "throughput_drop" in kinds(fired)


def test_straggler_needs_peers_then_fires():
    eng = DetectorEngine()
    # three peers at baseline, not enough to judge yet
    for g in range(3):
        assert not [a for a in eng.process(make_event(gpu=g, step_time_ms=180)) if a.kind == "straggler"]
    # fourth peer establishes the cohort
    eng.process(make_event(gpu=3, step_time_ms=180))
    fired = eng.process(make_event(gpu=0, step_time_ms=500))
    assert "straggler" in kinds(fired)


def test_ecc_accumulation():
    eng = DetectorEngine()
    fired = []
    for _ in range(3):
        fired = eng.process(make_event(ecc_errors=2))
    assert "ecc" in kinds(fired)


def test_oom_and_xid_and_thermal():
    eng = DetectorEngine()
    assert "oom" in kinds(eng.process(make_event(mem_used_gb=79.6)))
    assert "xid" in kinds(eng.process(make_event(xid_error=79)))
    assert "thermal" in kinds(eng.process(make_event(temp_c=91)))


def test_no_false_positive_on_steady_state():
    eng = DetectorEngine()
    rng = random.Random(3)
    anomalies = []
    for _ in range(50):
        anomalies += eng.process(make_event(step_time_ms=180 + rng.uniform(-4, 4)))
    assert anomalies == []
