import type { Summary } from "./types";

// Same-origin relative paths: proxied to the backend in dev, routed by
// CloudFront to the ALB/API origin in production.
export async function remediate(incidentId: string): Promise<void> {
  await fetch(`/incidents/${incidentId}/remediate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved_by: "operator" }),
  });
}

export async function getSummary(): Promise<Summary> {
  const res = await fetch("/history/summary");
  if (!res.ok) throw new Error(`summary ${res.status}`);
  return res.json();
}
