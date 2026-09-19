"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ApiError, Json, api, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Bar, Card, Empty, ErrorNote, ExperimentStatus, Pill } from "@/components/ui";

export default function Experiments() {
  const router = useRouter();
  const list = usePoll<Json>("/api/experiments?limit=60", 4000);
  const catalog = usePoll<Json>("/api/experiments/catalog", 0);
  const overview = usePoll<Json>("/api/overview", 0);
  const campaigns = usePoll<Json>("/api/campaigns", 4000);

  const [target, setTarget] = useState("");
  const [fault, setFault] = useState("stop_container");
  const [params, setParams] = useState<Record<string, string>>({});
  const [duration, setDuration] = useState(15);
  const [baseline, setBaseline] = useState(10);
  const [recovery, setRecovery] = useState(15);
  const [dryRun, setDryRun] = useState(true);
  const [rps, setRps] = useState(4);
  const [workloadN, setWorkloadN] = useState(100000);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const services: Json[] = (overview.data?.services ?? []).filter((s: Json) => !(catalog.data?.protected_targets ?? []).includes(s.name));
  const spec = useMemo(() => (catalog.data?.faults ?? []).find((f: Json) => f.fault_type === fault), [catalog.data, fault]);
  useEffect(() => { if (!target && services.length) setTarget(services[0].name); }, [services, target]);
  useEffect(() => setParams({}), [fault]);

  const allowedTargets = services.filter((s) => !spec || spec.target_kinds.includes(s.kind));

  async function launch() {
    setBusy(true);
    setMessage(null);
    try {
      const parameters: Record<string, number> = {};
      (spec?.parameters ?? []).forEach((p: Json) => {
        const raw = params[p.name];
        if (raw !== undefined && raw !== "") parameters[p.name] = Number(raw);
      });
      const exp = await api<Json>("/api/experiments", {
        method: "POST",
        body: { target, fault_type: fault, parameters, duration_s: duration, baseline_s: baseline, recovery_s: recovery, dry_run: dryRun, workload_rps: rps, workload_n: workloadN },
      });
      router.push(`/experiments/${exp.id}`);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function startSuite(reps: number) {
    setMessage(null);
    try {
      await api("/api/campaigns", { method: "POST", body: { suite: "standard", repetitions: reps } });
      campaigns.refresh();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    }
  }

  const latest = campaigns.data?.campaigns?.[0];

  return (
    <>
      <h1>Experiments</h1>
      <p className="sub">Controlled failure experiments. Real faults run only against allowlisted containers; dry runs apply nothing and act as controls.</p>
      <ErrorNote error={list.error} />

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <Card title="Launch an experiment">
          <div className="form">
            <label className="field"><span>Fault</span>
              <select value={fault} onChange={(e) => setFault(e.target.value)}>
                {(catalog.data?.faults ?? []).map((f: Json) => <option key={f.fault_type} value={f.fault_type}>{f.fault_type}</option>)}
              </select>
            </label>
            <label className="field"><span>Target</span>
              <select value={target} onChange={(e) => setTarget(e.target.value)}>
                {allowedTargets.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </label>
            {(spec?.parameters ?? []).map((p: Json) => (
              <label className="field" key={p.name}><span>{p.name}{p.unit ? ` (${p.unit})` : ""} {p.required ? "*" : ""}</span>
                <input type="number" min={p.min} max={p.max} step={p.type === "float" ? 0.05 : 1} placeholder={p.default != null ? String(p.default) : `${p.min}–${p.max}`}
                  value={params[p.name] ?? ""} onChange={(e) => setParams({ ...params, [p.name]: e.target.value })} />
              </label>
            ))}
            <label className="field"><span>Baseline (s)</span><input type="number" value={baseline} min={0} max={120} onChange={(e) => setBaseline(+e.target.value)} /></label>
            <label className="field"><span>Fault duration (s)</span><input type="number" value={duration} min={5} max={300} onChange={(e) => setDuration(+e.target.value)} /></label>
            <label className="field"><span>Recovery (s)</span><input type="number" value={recovery} min={5} max={300} onChange={(e) => setRecovery(+e.target.value)} /></label>
            <label className="field"><span>Workload (req/s)</span><input type="number" value={rps} min={0} max={25} onChange={(e) => setRps(+e.target.value)} /></label>
            <label className="field"><span>Request cost (n)</span><input type="number" value={workloadN} min={1} max={5000000} onChange={(e) => setWorkloadN(+e.target.value)} /></label>
            <label className="field"><span>Mode</span>
              <select value={dryRun ? "dry" : "real"} onChange={(e) => setDryRun(e.target.value === "dry")}>
                <option value="dry">dry run (control, nothing applied)</option>
                <option value="real">REAL fault</option>
              </select>
            </label>
          </div>
          {spec && <p className="note" style={{ marginTop: 10 }}>{spec.description}</p>}
          <div className="row" style={{ marginTop: 6 }}>
            <button disabled={busy || !target} onClick={launch} className={dryRun ? "" : "danger"}>{busy ? "Starting…" : dryRun ? "Run control (dry run)" : "Inject real fault"}</button>
            {message && <span className="err">{message}</span>}
          </div>
        </Card>

        <Card title="Campaigns" right={<Pill>standard suite: 8 scenarios</Pill>}>
          <p className="note" style={{ marginTop: 0 }}>Runs the standard resilience suite sequentially, interleaved across repetitions, so results reach usable confidence. One repetition takes about 8 minutes.</p>
          <div className="row" style={{ marginBottom: 12 }}>
            <button onClick={() => startSuite(1)}>Run once</button>
            <button onClick={() => startSuite(3)}>Run 3× (~25 min)</button>
          </div>
          {latest ? (
            <div>
              <div className="row spread"><b>{latest.name}</b><Pill tone={latest.status === "RUNNING" ? "blue" : latest.status === "COMPLETED" ? "green" : "amber"}>{latest.status}</Pill></div>
              <div style={{ margin: "8px 0" }}><Bar value={latest.completed} max={latest.total} tone="blue" /></div>
              <div className="note">{latest.completed} / {latest.total} experiments</div>
              {latest.status === "RUNNING" && (
                <button className="ghost" style={{ marginTop: 8 }} onClick={async () => { await api(`/api/campaigns/${latest.id}/abort`, { method: "POST" }); campaigns.refresh(); }}>Abort campaign</button>
              )}
            </div>
          ) : <Empty>No campaigns yet.</Empty>}
        </Card>
      </div>

      <Card title="History">
        <table>
          <thead><tr><th>When</th><th>Experiment</th><th>Status</th><th>Mode</th><th className="num">End-user impact</th><th>Affected</th><th className="num">Recovery</th></tr></thead>
          <tbody>
            {(list.data?.experiments ?? []).map((e: Json) => {
              const r = e.result;
              const eu = r?.entry_impact;
              return (
                <tr key={e.id} className="click" onClick={() => router.push(`/experiments/${e.id}`)}>
                  <td className="note">{fmt.datetime(e.timeline.created_at)}</td>
                  <td><Link href={`/experiments/${e.id}`}>{e.fault_type}</Link> on <b>{e.target}</b> <span className="note">{Object.keys(e.parameters || {}).length ? JSON.stringify(e.parameters) : ""}</span></td>
                  <td><ExperimentStatus status={e.status} /></td>
                  <td>{e.dry_run ? <Pill>control</Pill> : <Pill tone="red">real</Pill>}</td>
                  <td className="num">{eu ? fmt.pct(eu.impact_pct) : "–"}</td>
                  <td className="note">{(r?.affected_services ?? []).join(", ") || "–"}</td>
                  <td className="num">{r?.recovery?.recovery_s != null ? `${r.recovery.recovery_s} s` : "–"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!(list.data?.experiments ?? []).length && <Empty>No experiments yet.</Empty>}
      </Card>
    </>
  );
}
