"""FastAPI application: ingestion, health, incidents, and a live dashboard.

Runtime wiring (all in-process locally; the same stages are Lambdas + a
Step Functions workflow in AWS mode):

    POST /events -> EventStream(Kinesis) -> consumer -> DetectorEngine
                 -> IncidentManager(Step Functions) -> Bedrock RCA
                 -> WebSocket /live (dashboard) + alert (SNS)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from .archive import archive
from .detectors import DetectorEngine
from .models import Health, TelemetryEvent
from .store import store
from .stream import stream
from .workflow import IncidentManager

DASHBOARD = Path(__file__).resolve().parent / "dashboard.html"


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


class MetricsAggregator:
    """Live per-job cluster metrics derived from the latest sample per GPU."""

    def __init__(self) -> None:
        self._latest: dict[str, dict[tuple, TelemetryEvent]] = defaultdict(dict)
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=60))

    def update(self, e: TelemetryEvent) -> None:
        self._latest[e.job][(e.node, e.gpu)] = e

    def record_history(self) -> None:
        for job in self._latest:
            self._history[job].append(round(self._job_tokens(job)))

    def _job_tokens(self, job: str) -> float:
        return sum(e.tokens_per_s or 0 for e in self._latest[job].values())

    def snapshot(self, incident_mgr: IncidentManager) -> dict:
        open_by_job = {i.job: i for i in incident_mgr.open_incidents}
        jobs = []
        for job, gpus in self._latest.items():
            evs = list(gpus.values())
            inc = open_by_job.get(job)
            health = Health.healthy
            if inc:
                health = Health.down if inc.severity.value == "CRITICAL" else Health.degraded
            jobs.append(
                {
                    "job": job,
                    "health": health.value,
                    "gpus": len(evs),
                    "nodes": len({e.node for e in evs}),
                    "tokens_per_s": round(self._job_tokens(job)),
                    "avg_step_ms": round(sum(e.step_time_ms or 0 for e in evs) / len(evs), 1) if evs else 0,
                    "max_temp_c": round(max((e.temp_c for e in evs), default=0), 1),
                    "history": list(self._history[job]),
                    "incident": inc.model_dump() if inc else None,
                }
            )
        jobs.sort(key=lambda j: j["job"])
        return {"type": "snapshot", "jobs": jobs, "events_ingested": stream.published}


def create_app() -> FastAPI:
    app = FastAPI(title="The best GPU anomaly monitoring system ever", version="0.1.0")
    connections = ConnectionManager()
    detector = DetectorEngine()
    metrics = MetricsAggregator()

    async def actuator(proposal) -> None:
        """Apply the approved remediation to the (simulated) cluster.

        In the demo this cordons the target GPU and clears the injected fault
        so throughput visibly recovers. In AWS this would call the scheduler /
        fabric control plane instead.
        """
        sim = getattr(app.state, "simulator", None)
        if sim is None:
            return
        node, _, gpu = proposal.target.partition("/")
        if gpu.startswith("gpu"):
            try:
                sim.cordon(node, int(gpu[3:]))
            except ValueError:
                pass
        sim.clear_fault()

    incident_mgr = IncidentManager(broadcaster=connections.broadcast, actuator=actuator)

    async def consume() -> None:
        async for event in stream.subscribe():
            metrics.update(event)
            anomalies = detector.process(event)
            if anomalies:
                await incident_mgr.ingest_anomalies(event.job, anomalies)
                await connections.broadcast(
                    {"type": "anomaly", "anomalies": [a.model_dump() for a in anomalies]}
                )

    async def heartbeat() -> None:
        import time as _time

        while True:
            await asyncio.sleep(1.0)
            metrics.record_history()
            await incident_mgr.sweep()
            snap = metrics.snapshot(incident_mgr)
            # Firehose-equivalent: land a per-job rollup in the history archive.
            now = _time.time()
            for job in snap["jobs"]:
                archive.record(
                    {
                        "ts": now,
                        "job": job["job"],
                        "health": job["health"],
                        "tokens_per_s": job["tokens_per_s"],
                        "avg_step_ms": job["avg_step_ms"],
                        "max_temp_c": job["max_temp_c"],
                        "gpus": job["gpus"],
                        "incident_open": job["incident"] is not None,
                    }
                )
            await connections.broadcast(snap)

    async def demo_driver() -> None:
        """Built-in cluster simulator (enabled by ARGUS_DEMO=1)."""
        from .simulator import DEMO_TIMELINE, ClusterSimulator

        sim = ClusterSimulator()
        app.state.simulator = sim
        timeline = {start: (scen, dur) for start, scen, dur in DEMO_TIMELINE}
        period = float(os.environ.get("ARGUS_DEMO_PERIOD", "0.4"))
        loop_len = max(timeline) + 40
        while True:
            cursor = sim.step % loop_len
            if cursor in timeline and sim.active_fault is None:
                scen, dur = timeline[cursor]
                sim.inject(scen, steps=dur)
            for event in sim.tick():
                await stream.publish(event)
            await asyncio.sleep(period)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks = [asyncio.create_task(consume()), asyncio.create_task(heartbeat())]
        if os.environ.get("ARGUS_DEMO") == "1":
            tasks.append(asyncio.create_task(demo_driver()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)

    app.router.lifespan_context = lifespan

    # -- ingestion -----------------------------------------------------------
    @app.post("/events")
    async def ingest(events: list[TelemetryEvent] | TelemetryEvent):
        batch = events if isinstance(events, list) else [events]
        for e in batch:
            await stream.publish(e)
        return {"accepted": len(batch)}

    # -- read APIs -----------------------------------------------------------
    @app.get("/health")
    async def health():
        return metrics.snapshot(incident_mgr)

    @app.get("/incidents")
    async def incidents(limit: int = 50):
        return JSONResponse([i.model_dump() for i in store.list_incidents(limit)])

    @app.get("/incidents/{incident_id}")
    async def incident(incident_id: str):
        found = store.get_incident(incident_id)
        if not found:
            return JSONResponse({"error": "not found"}, status_code=404)
        return found.model_dump()

    # -- agentic remediation (approval-gated) --------------------------------
    @app.post("/incidents/{incident_id}/remediate")
    async def remediate(incident_id: str, body: dict | None = None):
        approved_by = (body or {}).get("approved_by", "operator")
        incident = await incident_mgr.remediate(incident_id, approved_by=approved_by)
        if incident is None:
            return JSONResponse({"error": "no incident or nothing to remediate"}, status_code=404)
        return incident.model_dump()

    # -- historical analytics (Firehose -> S3 -> Athena equivalent) ----------
    @app.get("/history/timeseries")
    async def history_timeseries(job: str, minutes: int = 30):
        return {"job": job, "minutes": minutes, "points": archive.timeseries(job, minutes)}

    @app.get("/history/summary")
    async def history_summary():
        return archive.incident_summary()

    # -- dashboard + live feed ----------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return DASHBOARD.read_text(encoding="utf-8")

    @app.websocket("/live")
    async def live(ws: WebSocket):
        await connections.connect(ws)
        await ws.send_json(metrics.snapshot(incident_mgr))
        try:
            while True:
                await ws.receive_text()  # keepalive; client never really sends
        except WebSocketDisconnect:
            connections.disconnect(ws)

    app.state.incident_mgr = incident_mgr
    return app


app = create_app()
