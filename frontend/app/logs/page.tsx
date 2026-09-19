"use client";

import { useState } from "react";
import { Json, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Card, Empty, ErrorNote, Pill } from "@/components/ui";

export default function Logs() {
  const [service, setService] = useState("");
  const [paused, setPaused] = useState(false);
  const overview = usePoll<Json>("/api/overview", 0);
  const feed = usePoll<Json>(`/api/events/recent?limit=120${service ? `&service=${service}` : ""}`, paused ? 0 : 2000);
  const events: Json[] = feed.data?.events ?? [];

  return (
    <>
      <h1>System Logs</h1>
      <p className="sub">Live feed of telemetry events and experiment lifecycle events (newest first).</p>
      <ErrorNote error={feed.error} />
      <div className="row" style={{ marginBottom: 12 }}>
        <select value={service} onChange={(e) => setService(e.target.value)}>
          <option value="">all services</option>
          {(overview.data?.services ?? []).map((s: Json) => <option key={s.name}>{s.name}</option>)}
        </select>
        <button className="ghost" onClick={() => setPaused(!paused)}>{paused ? "Resume" : "Pause"}</button>
        <span className="note">{paused ? "paused" : "updating every 2s"} · {events.length} events</span>
      </div>
      <Card>
        <table className="log">
          <thead><tr><th>Time</th><th>Source</th><th>Service</th><th>Event</th><th>Status</th><th className="num">Latency</th><th>Detail</th></tr></thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={i}>
                <td>{fmt.time(e.ts)}</td>
                <td><Pill tone={e.source === "experiment" ? "violet" : "grey"}>{e.source}</Pill></td>
                <td>{e.service}</td>
                <td>{e.type}</td>
                <td style={{ color: e.severity === "ERROR" ? "var(--red)" : e.severity === "WARN" ? "var(--amber)" : "inherit" }}>{e.status ?? ""}</td>
                <td className="num">{e.latency_ms != null ? fmt.ms(e.latency_ms) : ""}</td>
                <td className="note">{e.path ?? ""}{e.error ? ` · ${e.error}` : ""}{e.trace_id ? ` · ${String(e.trace_id).slice(0, 8)}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!events.length && <Empty>No events yet. Generate traffic (run an experiment with a workload, or the load generator).</Empty>}
      </Card>
    </>
  );
}
