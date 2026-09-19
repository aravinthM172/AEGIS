"use client";

import Link from "next/link";
import { Fragment, useState } from "react";
import { Json, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Bar, Card, ConfidencePill, Empty, ErrorNote, LevelPill, Pill, ScoreBar } from "@/components/ui";

export default function Resilience() {
  const { data, error } = usePoll<Json>("/api/resilience", 10000);
  const [open, setOpen] = useState<string | null>(null);
  const profiles: Json[] = Object.values(data?.services ?? {});
  const measured = profiles.filter((p) => p.worst_case_score != null).sort((a, b) => a.worst_case_score - b.worst_case_score);
  const unmeasured = profiles.filter((p) => p.worst_case_score == null);

  return (
    <>
      <h1>Resilience</h1>
      <p className="sub">Every score is 100 − the median <i>measured</i> impact when a dependency fault was injected. Untested combinations are listed, never scored.</p>
      <ErrorNote error={error} />

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <Card title="Critical dependencies" right={<Pill tone="violet">ranked by measured end-user impact</Pill>}>
          <table>
            <thead><tr><th>#</th><th>Dependency</th><th>Worst end-user impact</th><th className="num">Mean</th><th>Faults tried</th><th className="num">Runs</th></tr></thead>
            <tbody>
              {(data?.criticality ?? []).map((c: Json) => (
                <tr key={c.target}>
                  <td className="note">{c.rank}</td>
                  <td><b>{c.target}</b><div className="note mono">{c.propagation_paths.join("; ")}</div></td>
                  <td style={{ width: 170 }}>{c.end_user_impact_pct ? <div className="row"><Bar value={c.end_user_impact_pct.max} /><span className="mono">{fmt.pct(c.end_user_impact_pct.max)}</span></div> : <span className="note">no client data</span>}</td>
                  <td className="num">{fmt.pct(c.end_user_impact_pct?.mean)}</td>
                  <td className="note">{c.fault_types.join(", ")}</td>
                  <td className="num">{c.runs} <ConfidencePill value={c.confidence} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!(data?.criticality ?? []).length && <Empty>Run experiments to rank dependencies.</Empty>}
        </Card>

        <Card title="How much to trust this">
          <div className="banner" style={{ marginBottom: 10 }}>
            <b>Noise floor:</b>{" "}
            {data?.noise_floor?.controls
              ? <>{data.noise_floor.controls} control runs (no fault) showed up to <b>{fmt.pct(data.noise_floor.max_impact_pct)}</b> normal variation. An impact of that size is indistinguishable from noise.</>
              : "No control run yet: run a dry-run experiment with a workload to measure normal variation."}
          </div>
          <div className="note">Signatures used: {data?.signatures_used ?? "–"}. Thresholds: HIGH ≥ {data?.thresholds?.high}, MEDIUM ≥ {data?.thresholds?.medium}. Confidence is <i>low</i> below 3 runs of the same fault, <i>medium</i> at 3–4, <i>high</i> at 5+.</div>
        </Card>
      </div>

      <Card title="Per-service resilience profiles">
        <table>
          <thead><tr><th>Service</th><th>Worst case</th><th>Level</th><th>Average</th><th>Critical dependency</th><th>Confidence</th></tr></thead>
          <tbody>
            {measured.map((p) => (
              <Fragment key={p.service}>
                <tr className="click" onClick={() => setOpen(open === p.service ? null : p.service)}>
                  <td><b>{p.service === "end-users" ? "end users (whole system)" : p.service}</b></td>
                  <td><ScoreBar score={p.worst_case_score} /></td>
                  <td><LevelPill level={p.level} /></td>
                  <td className="num">{p.average_score}</td>
                  <td>{p.critical_dependency ? <>{p.critical_dependency.service} <span className="note">({p.critical_dependency.fault_type}, {p.critical_dependency.degraded_mode})</span></> : "–"}</td>
                  <td><ConfidencePill value={p.confidence} /></td>
                </tr>
                {open === p.service && (
                  <tr>
                    <td colSpan={6} style={{ background: "var(--panel-2)" }}>
                      <table>
                        <thead><tr><th>Dependency</th><th>Fault</th><th>Conditions</th><th className="num">Runs</th><th className="num">Impact (min / median / max)</th><th>Score</th><th>Mode</th><th className="num">Recovery</th></tr></thead>
                        <tbody>
                          {p.faults.map((f: Json, i: number) => (
                            <tr key={i}>
                              <td>{f.dependency}</td><td>{f.fault_type}</td>
                              <td className="note">{Object.entries(f.parameters || {}).map(([k, v]) => `${k}=${v}`).join(" ")} n={f.conditions?.workload_n}</td>
                              <td className="num">{f.runs}</td>
                              <td className="num">{fmt.pct(f.impact_pct.min)} / {fmt.pct(f.impact_pct.median)} / {fmt.pct(f.impact_pct.max)}</td>
                              <td><ScoreBar score={f.resilience_score} /></td>
                              <td className="note">{f.degraded_mode}</td>
                              <td className="num">{f.recovery_s ? `${f.recovery_s.median} s` : "–"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {p.untested_dependencies?.length > 0 && <div className="note" style={{ padding: "6px 8px" }}>Never tested: {p.untested_dependencies.join(", ")}</div>}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {unmeasured.length > 0 && (
          <>
            <h3>Not measured</h3>
            {unmeasured.map((p) => <div key={p.service} className="note"><Link href={`/services/${p.service}`}>{p.service}</Link>: {p.note}</div>)}
          </>
        )}
      </Card>
    </>
  );
}
