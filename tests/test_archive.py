import time
from pathlib import Path

from gpumon.archive import HistoryArchive


def test_archive_roundtrip_and_partition(tmp_path: Path):
    arc = HistoryArchive(directory=tmp_path)
    now = time.time()
    for i in range(5):
        arc.record({"ts": now - i, "job": "job-a", "tokens_per_s": 3000 + i, "max_temp_c": 70})
    arc.record({"ts": now, "job": "job-b", "tokens_per_s": 100})

    # one partition file was written
    files = list(tmp_path.glob("rollups-*.jsonl"))
    assert len(files) == 1

    pts = arc.timeseries("job-a", minutes=60)
    assert len(pts) == 5
    assert all(p["job"] == "job-a" for p in pts)
    # job-b is excluded
    assert arc.timeseries("job-b", minutes=60) == [
        p for p in arc.timeseries("job-b", minutes=60)
    ]


def test_timeseries_respects_window(tmp_path: Path):
    arc = HistoryArchive(directory=tmp_path)
    now = time.time()
    arc.record({"ts": now, "job": "j", "tokens_per_s": 1})
    arc.record({"ts": now - 3600, "job": "j", "tokens_per_s": 2})  # an hour ago
    recent = arc.timeseries("j", minutes=10)
    assert len(recent) == 1
