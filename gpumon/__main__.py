"""Command-line entry point.

    python -m gpumon demo      # server + built-in cluster simulator (one-command demo)
    python -m gpumon serve     # server only (feed it via POST /events)
    python -m gpumon simulate  # drive events over HTTP at a running server
    python -m gpumon loadtest   # throughput benchmark against the detector pipeline
"""

from __future__ import annotations

import argparse
import os
import sys


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("gpumon.api:app", host=host, port=port, log_level="info")


def cmd_demo(args: argparse.Namespace) -> None:
    os.environ["ARGUS_DEMO"] = "1"
    os.environ.setdefault("ARGUS_DEMO_PERIOD", str(args.period))
    print(f"Dashboard:  http://{args.host}:{args.port}/")
    print("Starting cluster simulator + monitoring pipeline...  (Ctrl-C to stop)")
    _serve(args.host, args.port)


def cmd_serve(args: argparse.Namespace) -> None:
    print(f"Dashboard:  http://{args.host}:{args.port}/")
    _serve(args.host, args.port)


def cmd_simulate(args: argparse.Namespace) -> None:
    import time

    import httpx

    from .simulator import DEMO_TIMELINE, ClusterSimulator

    sim = ClusterSimulator()
    timeline = {start: (scen, dur) for start, scen, dur in DEMO_TIMELINE}
    with httpx.Client(base_url=args.url, timeout=5.0) as client:
        try:
            while True:
                if sim.step in timeline and sim.active_fault is None:
                    scen, dur = timeline[sim.step]
                    sim.inject(scen, steps=dur)
                    print(f"[step {sim.step}] injecting scenario: {scen}")
                batch = [e.model_dump() for e in sim.tick()]
                client.post("/events", json=batch)
                time.sleep(args.period)
        except KeyboardInterrupt:
            print("\nstopped")


def cmd_loadtest(args: argparse.Namespace) -> None:
    """Measure detector-pipeline throughput in-process (no network)."""
    import time

    from .detectors import DetectorEngine
    from .simulator import ClusterSimulator

    sim = ClusterSimulator(nodes=args.nodes, gpus_per_node=8)
    detector = DetectorEngine()
    events = 0
    anomalies = 0
    start = time.perf_counter()
    while events < args.events:
        if events % 4000 == 0:
            sim.inject("straggler", steps=5)
        for e in sim.tick():
            anomalies += len(detector.process(e))
            events += 1
    elapsed = time.perf_counter() - start
    print(f"processed {events:,} events in {elapsed:.2f}s")
    print(f"throughput: {events / elapsed:,.0f} events/sec")
    print(f"per-event latency: {1e6 * elapsed / events:.1f} us")
    print(f"anomalies detected: {anomalies:,}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gpumon", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, fn in (("demo", cmd_demo), ("serve", cmd_serve)):
        p = sub.add_parser(name)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8000)
        p.add_argument("--period", type=float, default=0.4)
        p.set_defaults(func=fn)

    p = sub.add_parser("simulate")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--period", type=float, default=0.4)
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("loadtest")
    p.add_argument("--events", type=int, default=100_000)
    p.add_argument("--nodes", type=int, default=8)
    p.set_defaults(func=cmd_loadtest)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
