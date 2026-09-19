"use client";

import Link from "next/link";
import { Json, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { ActionStatus, Bar, Card, ConfidencePill, Empty, ErrorNote, ExperimentStatus, Pill, ServiceState } from "@/components/ui";

export default function Dashboard() {
  const { data, error } = usePoll<Json>("/api/overview", 3000);
  const services: Json[] = data?.services ?? [];
  const anomalous = services.filter((s) => s.status === "anomalous").length;
  const active = data?.active_experiment;

  return (
    <>
      <h1>Dashboard</h1>
      <p className="sub">Live state of the monitored workload, refreshed every 3 seconds.</p>
      <ErrorNote error={error} />

      {active && (
        <div className="banner" style={{ marginBottom: 14 }}>
          <div className="row spread">
            <div>
              <b>Experiment running:</b> {active.fault_type} on <b>{active.target}</b>{" "}
              <span className="note">
                ({active.dry_run ? "dry run" : "real fault"}
                {active.workload_rps ? `, ${active.workload_rps} rps workload` : ""})
              </span>
            </div>
            <div className="row">
              <Pill tone="blue">phase: {active.phase ?? active.status}</Pill>
              <Link href={`/experiments/${active.id}`}>watch live →</Link>
            </div>
          </div>
        </div>
      )}

      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Card tight><div className="label">Services</div><div className="stat">{services.length}</div></Card>
        <Card tight>
          <div className="label">Anomalous now</div>
          <div className="stat" style={{ color: anomalous ? "var(--red)" : "var(--green)" }}>{anomalous}</div>
        </Card>
        <Card tight><div className="label">Experiments run</div><div className="stat">{data?.counts?.experiments ?? "–"}</div></Card>
        <Card tight><div className="label">Failure signatures</div><div className="stat">{data?.counts?.signatures ?? "–"}</div></Card>
      </div>

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <Card title="Services">
          <div className="grid gs">
            {services.map((s) => (
              <Link key={s.name} href={`/services/${s.name}`} style={{ color: "inherit", textDecoration: "none" }}>
                <div className="card tight" style={{ background: "var(--panel-2)" }}>
                  <div className="row spread">
                    <b>{s.name}</b>
                    <Pill>{s.kind}</Pill>
                  </div>
                  <div style={{ margin: "6px 0 2px" }}><ServiceState status={s.status} modes={s.modes} /></div>
                  <div className="note">{s.total_events ? `${s.total_events} events · ${fmt.ago(s.last_seen)}` : "no telemetry emitted"}</div>
                </div>
              </Link>
            ))}
          </div>
        </Card>

        <Card title="Early warning" right={<Link href="/predictions">details →</Link>}>
          {data?.prediction?.status === "insufficient_data" ? (
            <Empty>No service has enough recent traffic to be judged. Prediction needs live requests.</Empty>
          ) : data?.prediction?.warnings?.length ? (
            data.prediction.warnings.map((w: Json) => (
              <div key={w.signature_id} className="banner warn" style={{ marginBottom: 8 }}>
                <div className="row spread">
                  <b>Resembles: {w.likely_fault} on {w.likely_target}</b>
                  <ConfidencePill value={w.confidence} />
                </div>
                <div className="note">
                  similarity {w.similarity} · {w.stage} · at risk: {w.at_risk_services.map((a: Json) => a.service).join(", ") || "none left"}
                </div>
              </div>
            ))
          ) : (
            <div><Pill tone="green">no warning</Pill> <span className="note">recent behaviour matches no known failure pattern.</span></div>
          )}
        </Card>
      </div>

      <div className="grid g2">
        <Card title="Most critical dependencies (measured end-user impact)" right={<Link href="/resilience">resilience →</Link>}>
          {data?.critical_dependencies?.length ? (
            <table>
              <tbody>
                {data.critical_dependencies.map((c: Json) => (
                  <tr key={c.target}>
                    <td style={{ width: 130 }}><b>{c.target}</b></td>
                    <td><Bar value={c.end_user_impact_pct?.max ?? 0} /></td>
                    <td className="num">{fmt.pct(c.end_user_impact_pct?.max)}</td>
                    <td className="note">{c.runs} runs</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No experiments measured yet.</Empty>}
          {data?.noise_floor?.max_impact_pct != null && (
            <div className="note" style={{ marginTop: 8 }}>
              Noise floor from {data.noise_floor.controls} control runs: up to {fmt.pct(data.noise_floor.max_impact_pct)} normal variation. Impacts near that are not distinguishable from noise.
            </div>
          )}
        </Card>

        <Card title="Recent experiments" right={<Link href="/experiments">all →</Link>}>
          <table>
            <tbody>
              {(data?.recent_experiments ?? []).map((e: Json) => (
                <tr key={e.id} className="click">
                  <td><Link href={`/experiments/${e.id}`}>{e.fault_type} on {e.target}</Link></td>
                  <td><ExperimentStatus status={e.status} /></td>
                  <td className="num">{e.result?.entry_impact ? fmt.pct(e.result.entry_impact.impact_pct) : "–"}</td>
                  <td className="note">{fmt.ago(e.timeline?.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!(data?.recent_experiments ?? []).length && <Empty>None yet.</Empty>}
          <h3>Recent remediation actions</h3>
          {(data?.recent_actions ?? []).length ? (
            <table>
              <tbody>
                {data.recent_actions.map((a: Json) => (
                  <tr key={a.id}>
                    <td>{a.action} → {a.target}</td>
                    <td><ActionStatus status={a.status} /></td>
                    <td className="note">{fmt.ago(a.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>No actions yet.</Empty>}
        </Card>
      </div>
    </>
  );
}
