"use client";

import { useState } from "react";
import { ApiError, Json, api, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Card, ConfidencePill, Empty, ErrorNote, Pill, ServiceState } from "@/components/ui";

export default function Predictions() {
  const live = usePoll<Json>("/api/predictions", 3000);
  const [backtest, setBacktest] = useState<Json | null>(null);
  const [running, setRunning] = useState(false);
  const [btError, setBtError] = useState<string | null>(null);
  const p = live.data;

  async function runBacktest() {
    setRunning(true);
    setBtError(null);
    try {
      setBacktest(await api<Json>("/api/predictions/backtest"));
    } catch (e) {
      setBtError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }
  const s = backtest?.summary;

  return (
    <>
      <h1>Predictions</h1>
      <p className="sub">Early warning by similarity to measured failure signatures. Accuracy is <b>only</b> claimed from the backtest below.</p>
      <ErrorNote error={live.error} />

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <Card title="Live service state" right={<span className="note">{p ? `window ${p.window_s}s · as of ${fmt.time(p.as_of)}` : ""}</span>}>
          {p?.status === "insufficient_data" && <div className="banner warn" style={{ marginBottom: 10 }}>{p.note}</div>}
          <table>
            <thead><tr><th>Service</th><th>State</th><th className="num">p95 ×</th><th className="num">Errors</th></tr></thead>
            <tbody>
              {Object.entries(p?.services ?? {}).map(([name, st]: [string, Json]) => (
                <tr key={name}>
                  <td><b>{name}</b></td>
                  <td><ServiceState status={st.status} modes={st.modes} /></td>
                  <td className="num">{st.p95_ratio ?? "–"}</td>
                  <td className="num">{st.error_pct != null ? fmt.pct(st.error_pct) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Warnings" right={<span className="note">{p?.signatures_available ?? 0} signatures known</span>}>
          {p?.warnings?.length ? p.warnings.map((w: Json) => (
            <div key={w.signature_id} className="banner warn" style={{ marginBottom: 10 }}>
              <div className="row spread"><b>Resembles: {w.likely_fault} on {w.likely_target}</b><ConfidencePill value={w.confidence} /></div>
              <div className="note" style={{ margin: "4px 0" }}>
                similarity {w.similarity} · stage <b>{w.stage}</b> · matched {w.matched_services.join(", ")} · supported by {w.supporting_signatures} signature(s)
              </div>
              {w.at_risk_services.length > 0 && <div>At risk next: {w.at_risk_services.map((a: Json) => <Pill key={a.service} tone="red">{a.service} · risk {a.risk}</Pill>)}</div>}
              <div className="note" style={{ marginTop: 4 }}>
                Last time: end-user impact {w.expected_end_user_impact_pct != null ? fmt.pct(w.expected_end_user_impact_pct) : "not measured"}, recovery {w.expected_recovery_s != null ? `${w.expected_recovery_s} s` : "n/a"}. Attribution can be wrong in the first seconds of an incident; see the backtest.
              </div>
            </div>
          )) : <div><Pill tone="green">no warning</Pill> <span className="note">recent behaviour matches no known failure.</span></div>}
        </Card>
      </div>

      <Card title="Measured accuracy (leave-one-out backtest)" right={<button onClick={runBacktest} disabled={running}>{running ? "Replaying…" : "Run backtest"}</button>}>
        {btError && <div className="err">{btError}</div>}
        {!s && !btError && <Empty>Replays every stored experiment second by second using only other experiments&apos; signatures. Takes several seconds.</Empty>}
        {s && (
          <>
            <div className="grid g4" style={{ marginBottom: 12 }}>
              {[
                ["Detection rate", s.detection_rate, `of ${s.fault_experiments_with_detectable_impact} detectable faults`],
                ["Correct attribution", s.correct_attribution_rate, "warning named the right fault and target"],
                ["False alarm rate", s.false_alarm_rate, `${s.false_alarm_steps}/${s.baseline_steps_evaluated} healthy steps`],
                ["Median lead time", s.median_lead_time_s, "seconds after the fault took effect"],
              ].map(([label, v, sub]) => (
                <div key={label as string} className="card tight">
                  <div className="label">{label}</div>
                  <div className="stat">{v == null ? "–" : label === "Median lead time" ? `${v} s` : `${Math.round((v as number) * 1000) / 10}%`}</div>
                  <div className="note">{sub}</div>
                </div>
              ))}
            </div>
            <div className="banner warn"><b>Read this before quoting any number:</b><ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{s.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul></div>
            <p className="note">{s.fault_experiments_without_detectable_impact} fault experiments had no measurable impact (nothing to detect) and {s.control_experiments} were controls.</p>
            <table>
              <thead><tr><th>Experiment</th><th>Detected</th><th>Correct</th><th className="num">Lead time</th><th>First warning named</th></tr></thead>
              <tbody>
                {backtest.experiments.filter((r: Json) => !r.dry_run).map((r: Json, i: number) => (
                  <tr key={i}>
                    <td>{r.fault_type} on <b>{r.target}</b> {!r.detectable && <Pill>no measurable impact</Pill>}</td>
                    <td>{r.detected ? <Pill tone="green">yes</Pill> : <Pill tone="grey">no</Pill>}</td>
                    <td>{r.correct_attribution ? <Pill tone="green">yes</Pill> : r.detected ? <Pill tone="red">wrong</Pill> : <Pill tone="grey">–</Pill>}</td>
                    <td className="num">{r.lead_time_s != null ? `${r.lead_time_s} s` : "–"}</td>
                    <td className="note">{r.first_warning_target ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Card>
    </>
  );
}
