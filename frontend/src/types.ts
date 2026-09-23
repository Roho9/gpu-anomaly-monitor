// Mirrors the pydantic models served by the gpumon backend.

export type Health = "HEALTHY" | "DEGRADED" | "DOWN";
export type Severity = "INFO" | "WARNING" | "CRITICAL";

export interface Anomaly {
  id: string;
  job: string;
  node: string;
  gpu: number | null;
  metric: string;
  value: number;
  baseline: number;
  zscore: number;
  severity: Severity;
  kind: string;
  message: string;
}

export interface Diagnosis {
  root_cause: string;
  confidence: number;
  reasoning: string;
  recommended_action: string;
  similar_incidents: string[];
  runbooks_used: string[];
  model: string;
}

export interface RemediationProposal {
  action: string;
  title: string;
  risk: "low" | "medium" | "high";
  requires_approval: boolean;
  target: string;
  steps: string[];
  rationale: string;
}

export interface RemediationRecord {
  proposal: RemediationProposal;
  status: "PROPOSED" | "APPROVED" | "EXECUTING" | "COMPLETED" | "FAILED";
  approved_by: string | null;
  audit: { ts: number; kind: string; detail: string }[];
}

export interface Incident {
  id: string;
  job: string;
  title: string;
  severity: Severity;
  status: string;
  anomalies: Anomaly[];
  diagnosis: Diagnosis | null;
  remediation: RemediationRecord | null;
}

export interface JobSnapshot {
  job: string;
  health: Health;
  gpus: number;
  nodes: number;
  tokens_per_s: number;
  avg_step_ms: number;
  max_temp_c: number;
  history: number[];
  incident: Incident | null;
}

export interface Snapshot {
  type: "snapshot";
  jobs: JobSnapshot[];
  events_ingested: number;
}

export interface Summary {
  total_incidents: number;
  resolved: number;
  by_kind: Record<string, number>;
  mean_time_to_resolve_s: number;
}

export interface FeedItem {
  id: number;
  time: string;
  severity: Severity;
  html: string;
}
