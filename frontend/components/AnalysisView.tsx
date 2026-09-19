"use client";

import { Json } from "@/lib/api";
import { Pill } from "@/components/ui";

export default function AnalysisView({ a }: { a: Json }) {
  if (a.status === "running") return <div className="banner">Local model is reading the evidence… (this can take a minute or two)</div>;
  const d = a.diagnosis;
  return (
    <div>
      <div className="row wrap" style={{ marginBottom: 8 }}>
        <Pill tone={a.status === "accepted" ? "green" : a.status === "rejected" ? "red" : "amber"}>{a.status}</Pill>
        {a.model && <span className="note">{a.model} · {a.attempts} attempt(s) · retrieval {a.retrieval}</span>}
        {a.status === "accepted" && <Pill tone="violet">LLM hypothesis, validated against evidence</Pill>}
      </div>
      <div className="banner" style={{ marginBottom: 10 }}><b>Measured (no model):</b> {a.deterministic_summary}</div>
      {a.status === "accepted" && d && (
        <>
          <h3>Root cause hypothesis <span className="note">(confidence {d.confidence})</span></h3>
          <p style={{ marginTop: 0 }}>{d.root_cause}</p>
          <h3>Evidence</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{d.evidence.map((f: Json, i: number) => <li key={i}>{f.claim} <span className="note mono">[{f.refs.join(", ")}]</span></li>)}</ul>
          <h3>Recommended actions</h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{d.recommended_actions.map((x: Json, i: number) => <li key={i}><Pill tone={x.kind === "mitigate" ? "amber" : "blue"}>{x.kind}</Pill> {x.action} {x.allowlisted_action !== "NONE" && <Pill tone="violet">{x.allowlisted_action}</Pill>}</li>)}</ul>
          {d.alternative_hypotheses.length > 0 && <><h3>Alternatives</h3><ul style={{ margin: 0, paddingLeft: 18 }}>{d.alternative_hypotheses.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></>}
          {d.limitations.length > 0 && <><h3>Limitations</h3><ul style={{ margin: 0, paddingLeft: 18 }}>{d.limitations.map((x: string, i: number) => <li key={i} className="note">{x}</li>)}</ul></>}
        </>
      )}
      {a.status !== "accepted" && (a.errors ?? []).length > 0 && (
        <div className="banner bad"><b>Not presented as fact:</b><ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{a.errors.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
      )}
    </div>
  );
}
