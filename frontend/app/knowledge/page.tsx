"use client";

import { useState } from "react";
import { ApiError, Json, api } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Card, Empty, ErrorNote, Pill } from "@/components/ui";

const TONE: Record<string, "blue" | "violet" | "amber"> = { runbook: "blue", architecture: "violet", experiment: "amber" };

export default function Knowledge() {
  const stats = usePoll<Json>("/api/knowledge", 0);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function search() {
    setError(null);
    try {
      setResults(await api<Json>(`/api/knowledge/search?q=${encodeURIComponent(q)}&k=6`));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }
  async function reindex() {
    setNote("Reindexing (embedding new chunks)…");
    try {
      const r = await api<Json>("/api/knowledge/reindex", { method: "POST" });
      setNote(`Indexed ${r.chunks} chunks (${r.changed} documents changed, ${r.newly_embedded} newly embedded), retrieval: ${r.retrieval}.`);
      stats.refresh();
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <>
      <h1>Knowledge</h1>
      <p className="sub">What the AI analyst can retrieve: runbooks, the architecture description, and an auto-generated report of every measured experiment.</p>
      <ErrorNote error={error || stats.error} />
      <div className="grid g3" style={{ marginBottom: 14 }}>
        <Card tight><div className="label">Chunks</div><div className="stat">{stats.data?.chunks ?? "–"}</div></Card>
        <Card tight>
          <div className="label">By source</div>
          <div className="row wrap" style={{ marginTop: 6 }}>{Object.entries(stats.data?.by_source ?? {}).map(([k, v]) => <Pill key={k} tone={TONE[k] ?? "blue"}>{k}: {String(v)}</Pill>)}</div>
        </Card>
        <Card tight>
          <div className="label">Retrieval</div>
          <div className="stat" style={{ fontSize: 20 }}>{stats.data?.retrieval ?? "–"}</div>
          <div className="note">{stats.data?.embedded_chunks ?? 0} embedded chunks</div>
        </Card>
      </div>

      <Card title="Search" right={<button className="ghost" onClick={reindex}>Reindex</button>}>
        <div className="row">
          <input style={{ flex: 1 }} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && q.trim() && search()} placeholder="e.g. why does database latency amplify request time?" />
          <button onClick={search} disabled={!q.trim()}>Search</button>
        </div>
        {note && <div className="note" style={{ marginTop: 6 }}>{note}</div>}
        {results && (
          <div style={{ marginTop: 12 }}>
            <div className="note">retrieval: {results.retrieval}</div>
            {results.results.map((r: Json) => (
              <div key={r.ref} className="card tight" style={{ marginTop: 8, background: "var(--panel-2)" }}>
                <div className="row spread"><b>{r.title}</b><Pill tone={TONE[r.source] ?? "blue"}>{r.source}</Pill></div>
                <div className="note mono">{r.ref}</div>
                <div style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{r.text.slice(0, 520)}{r.text.length > 520 ? "…" : ""}</div>
              </div>
            ))}
            {!results.results.length && <Empty>No matching knowledge.</Empty>}
          </div>
        )}
      </Card>
    </>
  );
}
