import { useState } from "react";
import { remediate } from "./api";
import type { FeedItem, Incident, JobSnapshot, Summary } from "./types";

export function Sparkline({ history }: { history: number[] }) {
  const w = 120;
  const h = 28;
  const n = history.length;
  if (n < 2) return <svg className="spark" width={w} height={h} />;
  const min = Math.min(...history);
  const max = Math.max(...history);
  const rng = max - min || 1;
  const pts = history
    .map((v, i) => `${(i / (n - 1)) * w},${h - ((v - min) / rng) * (h - 4) - 2}`)
    .join(" ");
  return (
    <svg className="spark" width={w} height={h}>
      <polyline fill="none" stroke="#5b9dff" strokeWidth={1.5} points={pts} />
    </svg>
  );
}

export function ClusterHealth({ jobs }: { jobs: JobSnapshot[] }) {
  return (
    <div className="card">
      <h2>Cluster health</h2>
      {jobs.length === 0 ? (
        <div className="empty">waiting for telemetry…</div>
      ) : (
        jobs.map((j) => (
          <div className="job" key={j.job}>
            <div className="name">{j.job}</div>
            <span className={`badge ${j.health}`}>{j.health}</span>
            <div className="metrics">
              <Sparkline history={j.history} />
              <span>
                <b>{j.tokens_per_s.toLocaleString()}</b> tok/s
              </span>
              <span>
                <b>{j.avg_step_ms}</b> ms/step
              </span>
              <span>
                <b>{j.max_temp_c}</b>°C
              </span>
              <span>
                <b>{j.gpus}</b> GPUs / {j.nodes} nodes
              </span>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function Remediation({ incident }: { incident: Incident }) {
  const [busy, setBusy] = useState(false);
  const r = incident.remediation;
  if (!r) return null;
  const p = r.proposal;

  const onApprove = async () => {
    setBusy(true);
    try {
      await remediate(incident.id);
    } catch {
      setBusy(false);
    }
  };

  let footer;
  if (r.status === "COMPLETED") {
    footer = (
      <div className="done">
        ✓ Remediation applied{r.approved_by ? ` (approved by ${r.approved_by})` : ""} · incident resolved
      </div>
    );
  } else if (r.status === "EXECUTING" || incident.status === "REMEDIATING") {
    footer = <div className="await">Executing remediation…</div>;
  } else {
    footer = (
      <>
        <button className="approve" onClick={onApprove} disabled={busy}>
          {busy ? "Executing…" : p.requires_approval ? "Approve & execute" : "Execute"}
        </button>
        <div className="await">
          {p.requires_approval
            ? "High-impact action - requires operator approval."
            : "Low-risk - eligible for auto-remediation."}
        </div>
      </>
    );
  }

  return (
    <div className="remedy">
      <h3>
        🤖 Proposed remediation <span className={`risk ${p.risk}`}>{p.risk} risk</span>
      </h3>
      <div className="v">
        <b>{p.title}</b> → <span className="mono muted">{p.target}</span>
      </div>
      <ol className="steps">
        {p.steps.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ol>
      {footer}
    </div>
  );
}

export function ActiveIncident({ jobs }: { jobs: JobSnapshot[] }) {
  const active = jobs
    .map((j) => j.incident)
    .filter((i): i is Incident => i !== null)
    .sort((a, b) => Number(b.severity === "CRITICAL") - Number(a.severity === "CRITICAL"))[0];

  if (!active) {
    return (
      <div className="card incident none">
        <h2>Active incident</h2>
        <div className="empty">No active incidents. All training jobs healthy.</div>
      </div>
    );
  }
  const d = active.diagnosis;
  return (
    <div className="card incident">
      <h2>Active incident</h2>
      <div>
        <span className={`pill ${active.severity}`}>{active.severity}</span>
        <span className="pill status">{active.status}</span>
      </div>
      <div className="inc-title">{active.title}</div>
      <div className="signals">{active.anomalies.map((a) => `• ${a.message}`).join("\n")}</div>
      {d ? (
        <div className="rca">
          <h3>
            🧠 AI root-cause analysis <span className="mono muted">{d.model}</span>
          </h3>
          <div className="k">Likely root cause</div>
          <div className="v">{d.root_cause}</div>
          <div className="k">Reasoning</div>
          <div className="v">{d.reasoning}</div>
          <div className="k">Recommended action</div>
          <div className="v">{d.recommended_action}</div>
          <div className="k">Confidence</div>
          <div className="conf">
            <i style={{ width: `${Math.round(d.confidence * 100)}%` }} />
          </div>
          {d.runbooks_used.length > 0 && (
            <>
              <div className="k">Grounded in</div>
              <div className="mono muted">
                {d.runbooks_used.join(", ")}
                {d.similar_incidents.length > 0 && ` · ${d.similar_incidents.length} similar past incident(s)`}
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="empty">Running Bedrock diagnosis…</div>
      )}
      <Remediation incident={active} />
    </div>
  );
}

export function LiveFeed({ feed }: { feed: FeedItem[] }) {
  return (
    <div className="card">
      <h2>Live signal feed</h2>
      <div className="feed">
        {feed.length === 0 ? (
          <div className="empty">no anomalies yet</div>
        ) : (
          feed.map((f) => (
            <div className="row" key={f.id}>
              <time>{f.time}</time>
              <span className={`dot ${f.severity}`} />
              <span className="msg" dangerouslySetInnerHTML={{ __html: f.html }} />
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export function Analytics({ summary }: { summary: Summary | null }) {
  const kinds = summary ? Object.entries(summary.by_kind) : [];
  const max = Math.max(1, ...kinds.map(([, n]) => n));
  return (
    <div className="card">
      <h2>
        History &amp; analytics <span className="mono muted src">Athena / S3</span>
      </h2>
      {!summary || summary.total_incidents === 0 ? (
        <div className="empty">no incidents recorded yet</div>
      ) : (
        <>
          <div className="kpis">
            <div className="kpi">
              <div className="n">{summary.total_incidents}</div>
              <div className="l">Incidents</div>
            </div>
            <div className="kpi">
              <div className="n">{summary.resolved}</div>
              <div className="l">Resolved</div>
            </div>
            <div className="kpi">
              <div className="n">{summary.mean_time_to_resolve_s ? `${summary.mean_time_to_resolve_s}s` : "--"}</div>
              <div className="l">Mean time to resolve</div>
            </div>
          </div>
          {kinds.map(([k, n]) => (
            <div className="kindbar" key={k}>
              <span className="lbl">{k}</span>
              <span className="bar" style={{ width: `${(n / max) * 140}px` }} />
              <span>{n}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
