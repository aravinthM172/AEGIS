"use client";

import Link from "next/link";
import { use, useState } from "react";
import { ApiError, Json, api, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import AnalysisView from "@/components/AnalysisView";
import { Bar, Card, Empty, ErrorNote, ExperimentStatus, KV, Pill } from "@/components/ui";

const FINAL = ["COMPLETED", "FAILED", "ABORTED", "ROLLBACK_FAILED"];

function Timeline({ t, dur }: { t: Json; dur: { baseline: number; fault: number; recovery: number } }) {
  const total = dur.baseline + dur.fault + dur.recovery || 1;
  const seg = (label: string, s: number, color: string) => (
    <div style={{ width: `${(s / total) * 100}%`, background: color }} title={`${label} ${s}s`}>{s >= total * 0.12 ? label : ""}</div>
  );
  return (
    <div>
      <div className="tl">{seg("baseline", dur.baseline, "#4cc2ff")}{seg("fault", dur.fault, "#ff5d6c")}{seg("recovery", dur.recovery, "#3ddc97")}</div>
      <div className="note" style={{ marginTop: 4 }}>created {fmt.time(t.created_at)} · fault applied {fmt.time(t.fault_applied_at)} · rollback {fmt.time(t.rollback_started_at)} · finished {fmt.time(t.finished_at)}</div>
    </div>
  );
}

export default function ExperimentDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const exp = usePoll<Json>(`/api/experiments/${id}`, 2000);
  const e = exp.data;
  const finished = !!e && FINAL.includes(e.status);
  const result = usePoll<Json>(finished ? `/api/experiments/${id}/result` : null, e && !e.result?.status ? 3000 : 0);
  const signature = usePoll<Json>(result.data?.status === "analyzed" ? `/api/experiments/${id}/signature` : null, 0);
  const [aiId, setAiId] = useState<string | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const analysis = usePoll<Json>(aiId ? `/api/analysis/${aiId}` : null, 3000);
  const [abortMsg, setAbortMsg] = useState<string | null>(null);

  const r = result.data?.status === "analyzed" ? result.data : null;
  const w = r?.windows;
  const dur = w ? { baseline: w.baseline.seconds, fault: w.fault.seconds, recovery: w.recovery.seconds }
    : { baseline: e?.baseline_s ?? 0, fault: e?.duration_s ?? 0, recovery: e?.recovery_s ?? 0 };

  async function askAi() {
    setAiError(null);
    try {
      const a = await api<Json>("/api/analysis", { method: "POST", body: { experiment_id: id } });
      setAiId(a.id);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <>
      <p className="note"><Link href="/experiments">← Experiments</Link></p>
      <ErrorNote error={exp.error} />
      {e && (
        <>
          <div className="row spread">
            <h1>{e.fault_type} on {e.target}</h1>
            <div className="row">
              <ExperimentStatus status={e.status} />
              {e.phase && <Pill tone="blue">{e.phase}</Pill>}
              {e.dry_run ? <Pill>control · nothing applied</Pill> : <Pill tone="red">real fault</Pill>}
              {(e.status === "RUNNING" || e.status === "PENDING") && (
                <button className="danger" onClick={async () => { try { await api(`/api/experiments/${id}/abort`, { method: "POST" }); } catch (x) { setAbortMsg(x instanceof ApiError ? x.message : String(x)); } }}>Abort</button>
              )}
            </div>
          </div>
          <p className="sub">{Object.keys(e.parameters).length ? JSON.stringify(e.parameters) : "no parameters"} · workload {e.workload_rps ? `${e.workload_rps} req/s (n=${e.workload_n})` : "none"} · {e.injector} injector</p>
          {abortMsg && <div className="banner bad" style={{ marginBottom: 12 }}>{abortMsg}</div>}
          {e.error && <div className="banner bad" style={{ marginBottom: 12 }}>{e.error}</div>}
          {e.hypothesis && <div className="banner" style={{ marginBottom: 12 }}>Hypothesis: {e.hypothesis}</div>}

          <Card title="Timeline"><Timeline t={e.timeline} dur={dur} /></Card>
        </>
      )}

      {finished && !r && <div className="banner" style={{ margin: "14px 0" }}>Analysing telemetry… the blast-radius analysis runs a few seconds after the experiment ends.</div>}

      {r && (
        <>
          <div className="grid g4" style={{ margin: "14px 0" }}>
            <Card tight><div className="label">End-user impact</div><div className="stat" style={{ color: (r.entry_impact?.impact_pct ?? 0) >= 10 ? "var(--red)" : "var(--green)" }}>{fmt.pct(r.entry_impact?.impact_pct)}</div>
              <div className="note">{r.entry_impact ? `${r.entry_impact.degraded_requests}/${r.entry_impact.total_requests} requests degraded` : "no client observer"}</div></Card>
            <Card tight><div className="label">Affected services</div><div className="stat">{r.affected_services.length}</div><div className="note">{r.affected_services.join(", ") || "none"}</div></Card>
            <Card tight><div className="label">Recovery</div><div className="stat">{r.recovery.recovery_s != null ? `${r.recovery.recovery_s} s` : "–"}</div><div className="note">{r.recovery.status}{r.recovery.restore_operation_s != null ? ` · restore took ${r.recovery.restore_operation_s} s` : ""}</div></Card>
            <Card tight><div className="label">Propagation</div><div style={{ fontWeight: 600, marginTop: 4 }}>{r.propagation.paths.join("; ") || "none"}</div></Card>
          </div>

          <Card title="Measured blast radius" right={<Pill tone="violet">basis: measured</Pill>}>
            <table>
              <thead><tr><th>Service</th><th>Role</th><th>Status</th><th>Impact</th><th className="num">p95 base → fault</th><th className="num">×</th><th className="num">Errors</th><th className="num">Requests b / f</th></tr></thead>
              <tbody>
                {Object.entries(r.services).map(([name, s]: [string, Json]) => (
                  <tr key={name}>
                    <td><b>{name}</b></td>
                    <td className="note">{s.role ?? "–"}</td>
                    <td><Pill tone={s.status === "affected" ? "red" : s.status === "unaffected" ? "green" : "amber"}>{s.status.replace("_", " ")}</Pill></td>
                    <td style={{ width: 150 }}>{s.impact_pct != null ? <div className="row"><Bar value={s.impact_pct} /><span className="mono">{fmt.pct(s.impact_pct)}</span></div> : <span className="note">not enough data</span>}</td>
                    <td className="num">{fmt.ms(s.baseline.p95_ms)} → {fmt.ms(s.fault.p95_ms)}</td>
                    <td className="num">{s.p95_ratio ?? "–"}</td>
                    <td className="num">{fmt.pct(s.fault.error_pct)}</td>
                    <td className="num">{s.baseline.requests} / {s.fault.requests}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="note" style={{ marginTop: 8 }}>Degraded = failed, or slower than {r.quality.slow_rule.replace("latency > ", "")}. A service needs at least {r.quality.min_events} requests per window to be judged.</div>
          </Card>

          <div className="grid g2" style={{ marginTop: 14 }}>
            <Card title="Structure vs measurement">
              <KV rows={[
                ["Predicted at risk", r.structural.at_risk.join(", ") || "–"],
                ["Measured affected", r.structural.measured_affected.join(", ") || "–"],
                ["At risk, but unaffected", r.structural.at_risk_but_unaffected.join(", ") || "–"],
                ["Not measurable", r.structural.at_risk_but_not_measurable.join(", ") || "–"],
                ["Affected, not predicted", r.structural.affected_but_not_predicted.join(", ") || "–"],
              ]} />
              <h3>Propagation edges</h3>
              {r.propagation.edges.length ? (
                <table><tbody>{r.propagation.edges.map((x: Json, i: number) => (
                  <tr key={i}><td>{x.source} → {x.target}</td><td><Pill tone={x.evidence === "observed" ? "blue" : "grey"}>{x.evidence}</Pill></td><td className="note">{x.calls_in_fault_window} calls, {x.failed_calls} failed</td></tr>
                ))}</tbody></table>
              ) : <Empty>None.</Empty>}
            </Card>
            <Card title="Quality &amp; signature">
              {r.quality.warnings.length ? r.quality.warnings.map((m: string, i: number) => <div key={i} className="banner warn" style={{ marginBottom: 6 }}>{m}</div>) : <Empty>No warnings.</Empty>}
              {r.workload && <><h3>Workload sent</h3><div className="note">{r.workload.sent} sent · {r.workload.ok} ok · {r.workload.failed} failed</div></>}
              {signature.data && <><h3>Failure signature</h3><KV rows={[["Kind", signature.data.kind], ["Fingerprint", <span key="f" className="mono">{signature.data.fingerprint}</span>]]} /></>}
            </Card>
          </div>

          <div style={{ marginTop: 14 }}>
            <Card title="AI analysis" right={<button onClick={askAi} disabled={!!aiId && analysis.data?.status === "running"}>Explain this experiment</button>}>
              {aiError && <div className="err">{aiError}</div>}
              {!aiId && !aiError && <Empty>Runs the local LLM over the measured evidence. Its answer is validated: unknown citations, unsupported services and invented numbers are rejected.</Empty>}
              {analysis.data && <AnalysisView a={analysis.data} />}
            </Card>
          </div>
        </>
      )}

      {e && (
        <div style={{ marginTop: 14 }}>
          <Card title="Lifecycle events">
            <table className="log"><tbody>
              {e.events.map((ev: Json, i: number) => (
                <tr key={i}><td>{fmt.time(ev.timestamp)}</td><td><b>{ev.event_type}</b></td><td className="note">{JSON.stringify(ev.detail).slice(0, 160)}</td></tr>
              ))}
            </tbody></table>
          </Card>
        </div>
      )}
    </>
  );
}
