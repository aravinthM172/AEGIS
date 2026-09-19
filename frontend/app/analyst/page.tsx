"use client";

import { useState } from "react";
import { ApiError, Json, api, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import AnalysisView from "@/components/AnalysisView";
import { Card, Empty, ErrorNote, Pill } from "@/components/ui";

const EXAMPLES = [
  "Why is the gateway vulnerable to PostgreSQL latency?",
  "What happens to users when cpp-service is stopped?",
  "Why did java-service returning errors affect the gateway?",
];

export default function Analyst() {
  const experiments = usePoll<Json>("/api/experiments?limit=60", 0);
  const history = usePoll<Json>("/api/analysis", 5000);
  const [question, setQuestion] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [current, setCurrent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const shown = usePoll<Json>(current ? `/api/analysis/${current}` : null, 3000);

  async function ask() {
    setError(null);
    try {
      const body: Json = {};
      if (experimentId) body.experiment_id = experimentId;
      if (question.trim()) body.question = question.trim();
      const a = await api<Json>("/api/analysis", { method: "POST", body });
      setCurrent(a.id);
      history.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <>
      <h1>AI Analyst</h1>
      <p className="sub">Ask why a failure behaved the way it did. The local model only sees measured evidence and retrieved runbooks, and its answer is rejected if it cites anything that isn&apos;t in the evidence.</p>
      <ErrorNote error={error} />

      <Card title="Ask">
        <div className="form" style={{ gridTemplateColumns: "1fr 320px auto" }}>
          <label className="field"><span>Question (optional if an experiment is chosen)</span>
            <textarea value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. Why is the gateway vulnerable to PostgreSQL latency?" />
          </label>
          <label className="field"><span>Experiment (optional: otherwise linked from the question)</span>
            <select value={experimentId} onChange={(e) => setExperimentId(e.target.value)}>
              <option value="">— best match for my question —</option>
              {(experiments.data?.experiments ?? []).filter((x: Json) => x.result?.status === "analyzed" && !x.dry_run).map((x: Json) => (
                <option key={x.id} value={x.id}>{x.fault_type} on {x.target} · {new Date(x.timeline.created_at).toLocaleTimeString()}</option>
              ))}
            </select>
          </label>
          <button onClick={ask} disabled={!question.trim() && !experimentId}>Analyse</button>
        </div>
        <div className="row wrap" style={{ marginTop: 8 }}>
          {EXAMPLES.map((q) => <button key={q} className="ghost" onClick={() => setQuestion(q)}>{q}</button>)}
        </div>
      </Card>

      {shown.data && (
        <div style={{ marginTop: 14 }}>
          <Card title={shown.data.question}>
            <AnalysisView a={shown.data} />
            {shown.data.evidence?.valid_refs && (
              <>
                <h3>Evidence the model was allowed to cite</h3>
                <div className="row wrap">{shown.data.evidence.valid_refs.map((r: string) => <Pill key={r}>{r.length > 44 ? r.slice(0, 44) + "…" : r}</Pill>)}</div>
              </>
            )}
          </Card>
        </div>
      )}

      <div style={{ marginTop: 14 }}>
        <Card title="History">
          <table>
            <thead><tr><th>When</th><th>Question</th><th>Status</th><th>Model</th></tr></thead>
            <tbody>
              {(history.data?.analyses ?? []).map((a: Json) => (
                <tr key={a.id} className="click" onClick={() => setCurrent(a.id)}>
                  <td className="note">{fmt.datetime(a.created_at)}</td>
                  <td>{a.question.slice(0, 90)}</td>
                  <td><Pill tone={a.status === "accepted" ? "green" : a.status === "running" ? "blue" : a.status === "rejected" ? "red" : "amber"}>{a.status}</Pill></td>
                  <td className="note">{a.model ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!(history.data?.analyses ?? []).length && <Empty>No analyses yet.</Empty>}
        </Card>
      </div>
    </>
  );
}
