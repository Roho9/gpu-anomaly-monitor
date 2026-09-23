import { useEffect, useRef, useState } from "react";
import { getSummary } from "./api";
import type { FeedItem, JobSnapshot, Severity, Summary } from "./types";

interface LiveData {
  connected: boolean;
  jobs: JobSnapshot[];
  eventsIngested: number;
  feed: FeedItem[];
  summary: Summary | null;
}

const now = () => new Date().toLocaleTimeString([], { hour12: false });

export function useLiveData(): LiveData {
  const [connected, setConnected] = useState(false);
  const [jobs, setJobs] = useState<JobSnapshot[]>([]);
  const [eventsIngested, setEventsIngested] = useState(0);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const feedId = useRef(0);

  const push = (severity: Severity, html: string) => {
    setFeed((prev) => {
      const item: FeedItem = { id: feedId.current++, time: now(), severity, html };
      return [item, ...prev].slice(0, 60);
    });
  };

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout>;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/live`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        switch (m.type) {
          case "snapshot":
            setJobs(m.jobs);
            setEventsIngested(m.events_ingested);
            break;
          case "anomaly":
            m.anomalies.forEach((a: { severity: Severity; kind: string; message: string }) =>
              push(a.severity, `<span class="mono muted">[${a.kind}]</span> ${a.message}`),
            );
            break;
          case "incident_opened":
            push("CRITICAL", `<b>Incident opened:</b> ${m.incident.title}`);
            break;
          case "incident_diagnosed":
            push("INFO", `<b>Diagnosed:</b> ${m.incident.diagnosis.root_cause}`);
            break;
          case "remediation_started":
            push("WARNING", `\u{1F916} <b>Remediation started:</b> ${m.incident.remediation.proposal.title}`);
            break;
          case "remediation_completed":
            push("INFO", `✓ <b>Remediation applied:</b> ${m.incident.remediation.proposal.action}`);
            break;
          case "incident_resolved":
            push("INFO", `<b>Resolved:</b> ${m.incident.title}`);
            break;
          case "alert":
            push(m.severity, `\u{1F514} <b>Alert sent:</b> ${m.title} → ${m.action ?? ""}`);
            break;
        }
      };
    };
    connect();

    const poll = setInterval(() => {
      getSummary().then(setSummary).catch(() => undefined);
    }, 5000);
    getSummary().then(setSummary).catch(() => undefined);

    return () => {
      closed = true;
      clearTimeout(retry);
      clearInterval(poll);
      ws?.close();
    };
  }, []);

  return { connected, jobs, eventsIngested, feed, summary };
}
