"use client";

import { useState } from "react";
import { ApiError, Json, api } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Card, Empty, ErrorNote, Pill } from "@/components/ui";

const TONE: Record<string, "red" | "amber" | "blue" | "grey"> = { critical: "red", high: "red", medium: "amber", low: "blue" };

export default function Incidents() {
  const { data, error, refresh } = usePoll<Json>("/api/incidents", 5000);
  const [service, setService] = useState("gateway");
  const [severity, setSeverity] = useState("high");
  const [message, setMessage] = useState("");
  const [note, setNote] = useState<string | null>(null);

  async function report() {
    setNote(null);
    try {
      const q = new URLSearchParams({ service, severity, message });
      await api(`/api/incidents?${q.toString()}`, { method: "POST" });
      setMessage("");
      refresh();
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <>
      <h1>Incidents</h1>
      <p className="sub">Incidents flow API → Kafka → the Java consumer, and update per-service state in Redis. (Failure experiments are recorded separately under Experiments.)</p>
      <ErrorNote error={error} />
      <Card title="Report an incident">
        <div className="form">
          <label className="field"><span>Service</span><input value={service} onChange={(e) => setService(e.target.value)} /></label>
          <label className="field"><span>Severity</span>
            <select value={severity} onChange={(e) => setSeverity(e.target.value)}>{["low", "medium", "high", "critical"].map((s) => <option key={s}>{s}</option>)}</select>
          </label>
          <label className="field" style={{ gridColumn: "span 2" }}><span>Message</span><input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="what happened?" /></label>
          <button disabled={!message.trim()} onClick={report}>Report</button>
        </div>
        {note && <div className="err" style={{ marginTop: 8 }}>{note}</div>}
      </Card>
      <div style={{ height: 14 }} />
      <Card title={`Incidents (${data?.count ?? 0})`}>
        <table>
          <thead><tr><th>#</th><th>Service</th><th>Severity</th><th>Message</th><th>Status</th></tr></thead>
          <tbody>
            {(data?.incidents ?? []).map((i: Json) => (
              <tr key={i.id}><td className="note">{i.id}</td><td><b>{i.service}</b></td><td><Pill tone={TONE[i.severity] ?? "grey"}>{i.severity}</Pill></td><td>{i.message}</td><td><Pill>{i.status}</Pill></td></tr>
            ))}
          </tbody>
        </table>
        {!(data?.incidents ?? []).length && <Empty>No incidents.</Empty>}
      </Card>
    </>
  );
}
