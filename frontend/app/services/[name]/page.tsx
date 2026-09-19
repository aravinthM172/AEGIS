"use client";

import Link from "next/link";
import { use } from "react";
import { Json, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Card, ConfidencePill, Empty, ErrorNote, KV, LevelPill, Pill, ScoreBar } from "@/components/ui";

export default function ServiceDetail({ params }: { params: Promise<{ name: string }> }) {
  const { name } = use(params);
  const svc = usePoll<Json>(`/api/services/${name}`, 5000);
  const dependents = usePoll<Json>(`/api/services/${name}/dependents?basis=combined&window_minutes=1440`, 0);
  const resilience = usePoll<Json>(`/api/resilience/${name}`, 0);
  const s = svc.data;
  const profile = resilience.data;

  return (
    <>
      <p className="note"><Link href="/services">← Services</Link></p>
      <h1>{name}</h1>
      <p className="sub">{s?.description}</p>
      <ErrorNote error={svc.error} />

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <Card title="Identity">
          {s && (
            <KV rows={[
              ["Kind", <Pill key="k">{s.kind}</Pill>],
              ["Technology", s.technology],
              ["Container", <span key="c" className="mono">{s.container}</span>],
              ["Events emitted", s.telemetry?.total_events ?? "none (not instrumented)"],
              ["Last seen", fmt.ago(s.telemetry?.last_seen)],
            ]} />
          )}
        </Card>
        <Card title="If this service fails, who is at risk? (declared + observed graph)">
          {dependents.data?.at_risk?.length ? (
            <table>
              <tbody>
                {dependents.data.at_risk.map((a: Json) => (
                  <tr key={a.service}><td><Link href={`/services/${a.service}`}>{a.service}</Link></td><td className="note">{a.hops} hop{a.hops > 1 ? "s" : ""}</td></tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>Nothing depends on it.</Empty>}
          <div className="note">Structural prediction, not measured impact. See Resilience for measured results.</div>
        </Card>
      </div>

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <Card title="Depends on">
          {s?.depends_on?.length ? (
            <table>
              <tbody>
                {s.depends_on.map((e: Json) => (
                  <tr key={e.target + e.relation}>
                    <td><Link href={`/services/${e.target}`}><b>{e.target}</b></Link></td>
                    <td><Pill>{e.relation}</Pill></td>
                    <td className="note">{e.evidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No dependencies.</Empty>}
        </Card>
        <Card title="Depended on by">
          {s?.depended_on_by?.length ? (
            <table>
              <tbody>
                {s.depended_on_by.map((e: Json) => (
                  <tr key={e.source + e.relation}>
                    <td><Link href={`/services/${e.source}`}><b>{e.source}</b></Link></td>
                    <td><Pill>{e.relation}</Pill></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>Nothing declares a dependency on it.</Empty>}
        </Card>
      </div>

      <Card title="Measured resilience" right={profile ? <ConfidencePill value={profile.confidence} /> : null}>
        {profile ? (
          <>
            <div className="row" style={{ marginBottom: 10 }}>
              <span className="label">worst case</span><ScoreBar score={profile.worst_case_score} /><LevelPill level={profile.level} />
              {profile.critical_dependency && <span className="note">critical dependency: <b>{profile.critical_dependency.service}</b> ({profile.critical_dependency.fault_type})</span>}
            </div>
            <table>
              <thead><tr><th>Dependency fault</th><th>Runs</th><th className="num">Impact</th><th>Score</th><th>Mode</th></tr></thead>
              <tbody>
                {profile.faults.map((f: Json, i: number) => (
                  <tr key={i}>
                    <td>{f.dependency} · {f.fault_type}</td>
                    <td className="num">{f.runs}</td>
                    <td className="num">{fmt.pct(f.impact_pct.median)}</td>
                    <td><ScoreBar score={f.resilience_score} /></td>
                    <td className="note">{f.degraded_mode}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {profile.untested_dependencies?.length > 0 && <div className="note" style={{ marginTop: 8 }}>Never tested (not scored): {profile.untested_dependencies.join(", ")}</div>}
          </>
        ) : <Empty>{resilience.error ?? "No experiment has measured this service yet."}</Empty>}
      </Card>
    </>
  );
}
