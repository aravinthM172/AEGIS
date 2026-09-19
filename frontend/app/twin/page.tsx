"use client";

import { useState } from "react";
import { ApiError, Json, api, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Bar, Card, Empty, ErrorNote, Pill } from "@/components/ui";

const FAULTS = ["stop_container", "pause_container", "latency", "cpu_stress", "http_error", "memory_stress"];

export default function Twin() {
  const overview = usePoll<Json>("/api/overview", 0);
  const topo = usePoll<Json>("/api/topology", 0);
  const validation = usePoll<Json>("/api/twin/validation", 0);
  const [target, setTarget] = useState("cpp-service");
  const [fault, setFault] = useState("stop_container");
  const [change, setChange] = useState("none");
  const [edge, setEdge] = useState("");
  const [hit, setHit] = useState(0.8);
  const [count, setCount] = useState(2);
  const [result, setResult] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);

  const edges: Json[] = (topo.data?.edges ?? []).filter((e: Json) => e.relation === "calls" || e.target !== "kafka");
  const services: Json[] = overview.data?.services ?? [];

  async function run() {
    setError(null);
    try {
      if (change === "none") {
        const r = await api<Json>(`/api/twin/what-if?target=${target}&fault_type=${fault}`);
        setResult({ measured_baseline: r.measured, simulated_baseline: r.simulated, simulated_with_change: null });
        return;
      }
      const mutation: Json = { type: change };
      if (change === "replicas") { mutation.service = target; mutation.count = count; }
      else { const [s, t] = (edge || `${edges[0]?.source}>${edges[0]?.target}`).split(">"); mutation.source = s; mutation.target = t; if (change === "fallback") mutation.hit_ratio = hit; }
      setResult(await api<Json>("/api/twin/architecture", { method: "POST", body: { target, fault_type: fault, mutations: [mutation] } }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setResult(null);
    }
  }

  const v = validation.data;
  const Side = ({ title, tone, r, children }: { title: string; tone: "green" | "blue" | "violet"; r: Json | null; children?: React.ReactNode }) => (
    <Card title={title} right={<Pill tone={tone}>{r?.basis ?? "n/a"}</Pill>}>
      {r ? (
        <>
          <div className="stat" style={{ color: (r.end_user_impact_pct ?? 0) >= 10 ? "var(--red)" : "var(--green)" }}>{fmt.pct(r.end_user_impact_pct)}</div>
          <div className="label">end-user impact{r.runs ? ` · ${r.runs} real runs · ${r.confidence} confidence` : ""}</div>
          <table style={{ marginTop: 10 }}><tbody>
            {Object.entries(r.service_impact_pct ?? {}).map(([n, val]) => (
              <tr key={n}><td>{n}</td><td style={{ width: 140 }}><Bar value={val as number} /></td><td className="num">{fmt.pct(val as number)}</td></tr>
            ))}
          </tbody></table>
          {children}
        </>
      ) : <Empty>{title.startsWith("Measured") ? "No experiment has measured this combination." : "–"}</Empty>}
    </Card>
  );

  return (
    <>
      <h1>Digital twin</h1>
      <p className="sub">A simplified model learned from measured experiments. It answers “what if X fails?” and “what if we change the architecture?”, and it always says which numbers are <b>measured</b>, which are <b>simulated</b>, and what it <b>assumed</b>.</p>
      <ErrorNote error={error} />

      <Card title="Ask the twin">
        <div className="form">
          <label className="field"><span>What fails</span><select value={target} onChange={(e) => setTarget(e.target.value)}>{services.map((s) => <option key={s.name}>{s.name}</option>)}</select></label>
          <label className="field"><span>How</span><select value={fault} onChange={(e) => setFault(e.target.value)}>{FAULTS.map((f) => <option key={f}>{f}</option>)}</select></label>
          <label className="field"><span>Architecture change</span>
            <select value={change} onChange={(e) => setChange(e.target.value)}>
              <option value="none">none: current architecture</option>
              <option value="queue">put a queue on a call</option>
              <option value="fallback">add a fallback on a call</option>
              <option value="replicas">run replicas of the failing service</option>
            </select>
          </label>
          {(change === "queue" || change === "fallback") && (
            <label className="field"><span>Which call</span>
              <select value={edge} onChange={(e) => setEdge(e.target.value)}>
                {edges.map((e) => <option key={e.source + e.target} value={`${e.source}>${e.target}`}>{e.source} → {e.target}</option>)}
              </select>
            </label>
          )}
          {change === "fallback" && <label className="field"><span>Fallback hit ratio</span><input type="number" min={0} max={1} step={0.1} value={hit} onChange={(e) => setHit(+e.target.value)} /></label>}
          {change === "replicas" && <label className="field"><span>Replicas</span><input type="number" min={2} max={10} value={count} onChange={(e) => setCount(+e.target.value)} /></label>}
          <button onClick={run}>Simulate</button>
        </div>
      </Card>

      {result && (
        <div className="grid g3" style={{ margin: "14px 0" }}>
          <Side title="Measured (real experiments)" tone="green" r={result.measured_baseline} />
          <Side title="Simulated: current architecture" tone="blue" r={result.simulated_baseline}>
            {result.simulated_baseline?.assumptions?.map((a: string, i: number) => <div key={i} className="banner warn" style={{ marginTop: 8 }}>{a}</div>)}
          </Side>
          {result.simulated_with_change
            ? <Side title="Simulated: with the change" tone="violet" r={result.simulated_with_change}>
                {result.simulated_with_change.assumptions.map((a: string, i: number) => <div key={i} className="banner warn" style={{ marginTop: 8 }}>Assumes: {a}</div>)}
                {result.simulated_with_change.notes.map((a: string, i: number) => <div key={i} className="note" style={{ marginTop: 6 }}>{a}</div>)}
              </Side>
            : <Card title="With a change"><Empty>Pick an architecture change to simulate it.</Empty></Card>}
        </div>
      )}
      {result?.disclaimer && <div className="banner" style={{ marginBottom: 14 }}>{result.disclaimer}</div>}

      <Card title="How accurate is the twin?" right={<Pill tone="violet">leave-one-combination-out</Pill>}>
        {v ? (
          <>
            <div className="row wrap" style={{ marginBottom: 10 }}>
              <span><b>{v.combinations_tested}</b> hidden combinations</span>
              <span>· mean error <b>{v.mean_abs_error_pct} points</b></span>
              <span>· affected-or-not correct <b>{Math.round(v.affected_or_not_accuracy * 100)}%</b></span>
            </div>
            <table>
              <thead><tr><th>Hidden fault</th><th className="num">Measured</th><th className="num">Predicted</th><th className="num">Error</th><th>Assumed hard (never measured)</th></tr></thead>
              <tbody>
                {v.rows.map((r: Json, i: number) => (
                  <tr key={i}>
                    <td>{r.fault_type} on <b>{r.target}</b> {!r.affected_class_correct && <Pill tone="red">wrong call</Pill>}</td>
                    <td className="num">{fmt.pct(r.measured_end_user_impact_pct)}</td>
                    <td className="num">{fmt.pct(r.predicted_end_user_impact_pct)}</td>
                    <td className="num">{r.abs_error_pct}</td>
                    <td className="note">{r.assumed_edges.join(", ") || "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <ul className="note">{v.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
          </>
        ) : <Empty>{validation.error ?? "Loading…"}</Empty>}
      </Card>
    </>
  );
}
