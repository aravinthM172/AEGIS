"use client";

import { useState } from "react";
import { ApiError, Json, api, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { ActionStatus, Card, Empty, ErrorNote, Pill } from "@/components/ui";

const ACTIONS = ["RESTART_SERVICE", "SCALE_SERVICE", "CLEAR_CACHE", "ROLLBACK_DEPLOYMENT", "ENABLE_FALLBACK"];

export default function Actions() {
  const actions = usePoll<Json>("/api/actions?limit=40", 3000);
  const recs = usePoll<Json>("/api/remediation/recommendations", 4000);
  const audit = usePoll<Json>("/api/audit-log?limit=40", 5000);
  const overview = usePoll<Json>("/api/overview", 0);
  const [action, setAction] = useState("RESTART_SERVICE");
  const [target, setTarget] = useState("cpp-service");
  const [mode, setMode] = useState("dry_run");
  const [msg, setMsg] = useState<string | null>(null);

  async function submit(a: string, t: string, m: string) {
    setMsg(null);
    try {
      await api("/api/actions", { method: "POST", body: { action: a, target: t, mode: m } });
      actions.refresh();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    }
  }
  async function decide(id: string, verb: "approve" | "reject") {
    setMsg(null);
    try {
      await api(`/api/actions/${id}/${verb}`, { method: "POST" });
      actions.refresh();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <>
      <h1>Actions</h1>
      <p className="sub">Policy-controlled remediation. The model can only name an allowlisted action; the policy engine decides, an administrator approves risky ones, and recovery is verified with real probes.</p>
      <ErrorNote error={actions.error} />
      {msg && <div className="banner bad" style={{ marginBottom: 12 }}>{msg}</div>}

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <Card title="Propose an action">
          <div className="form">
            <label className="field"><span>Action</span><select value={action} onChange={(e) => setAction(e.target.value)}>{ACTIONS.map((a) => <option key={a}>{a}</option>)}</select></label>
            <label className="field"><span>Target</span>
              <select value={target} onChange={(e) => setTarget(e.target.value)}>
                {(overview.data?.services ?? []).map((s: Json) => <option key={s.name}>{s.name}</option>)}
              </select>
            </label>
            <label className="field"><span>Mode</span>
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="dry_run">dry run (record the policy verdict only)</option>
                <option value="execute">execute (if policy allows)</option>
              </select>
            </label>
            <button className={mode === "execute" ? "danger" : ""} onClick={() => submit(action, target, mode)}>{mode === "execute" ? "Execute" : "Evaluate"}</button>
          </div>
          <p className="note">Not on the allowlist, protected targets, actions during an experiment, restart loops and stateful restarts are all refused or held for approval.</p>
        </Card>

        <Card title="Suggestions from live prediction">
          {recs.data?.suggestions?.length ? recs.data.suggestions.map((s: Json, i: number) => (
            <div key={i} className="banner warn" style={{ marginBottom: 8 }}>
              <div className="row spread">
                <div><b>{s.suggested_action}</b> on <b>{s.target}</b> <span className="note">← {s.warning.likely_fault} (sim {s.warning.similarity}, {s.warning.confidence})</span></div>
                <Pill tone={s.policy.verdict === "allow" ? "green" : s.policy.verdict === "needs_approval" ? "amber" : "red"}>{s.policy.verdict}</Pill>
              </div>
              <div className="row" style={{ marginTop: 6 }}>
                <button className="ghost" onClick={() => submit(s.suggested_action, s.target, "dry_run")}>Evaluate</button>
                <button onClick={() => submit(s.suggested_action, s.target, "execute")}>Execute</button>
              </div>
            </div>
          )) : <Empty>No actionable suggestion. Suggestions only appear for a recognised, developing failure with a known remedy; a latency pattern, for example, has none.</Empty>}
        </Card>
      </div>

      <Card title="Actions">
        <table>
          <thead><tr><th>When</th><th>Action</th><th>Status</th><th>Risk</th><th>Policy</th><th>Verification</th><th></th></tr></thead>
          <tbody>
            {(actions.data?.actions ?? []).map((a: Json) => (
              <tr key={a.id}>
                <td className="note">{fmt.ago(a.created_at)}</td>
                <td><b>{a.action}</b> → {a.target}<div className="note">{a.mode} · by {a.requested_by} · {a.source}</div></td>
                <td><ActionStatus status={a.status} /></td>
                <td>{a.policy?.risk && a.policy.risk !== "n/a" ? <Pill tone={a.policy.risk === "high" ? "amber" : "blue"}>{a.policy.risk}</Pill> : "–"}</td>
                <td className="note" style={{ maxWidth: 320 }}>{a.policy?.reasons?.[0]}</td>
                <td className="note">
                  {a.verification ? (a.verification.verified ? <span className="ok">verified · recovery {a.verification.recovery_time_s}s</span> : <span className="err">{a.verification.reason}</span>) : a.error ? <span className="err">{a.error}</span> : "–"}
                </td>
                <td>
                  {a.status === "PENDING_APPROVAL" && (
                    <div className="row">
                      <button onClick={() => decide(a.id, "approve")}>Approve</button>
                      <button className="ghost" onClick={() => decide(a.id, "reject")}>Reject</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!(actions.data?.actions ?? []).length && <Empty>No actions yet.</Empty>}
        <div className="note" style={{ marginTop: 6 }}>Approve / reject need an <b>admin</b> API key.</div>
      </Card>

      <div style={{ marginTop: 14 }}>
        <Card title="Audit log" right={<span className="note">append-only</span>}>
          {audit.error ? <div className="note">{audit.error}</div> : (
            <table className="log"><tbody>
              {(audit.data?.entries ?? []).map((e: Json) => (
                <tr key={e.id}><td>{fmt.time(e.ts)}</td><td>{e.actor}</td><td><b>{e.event}</b></td><td className="note">{JSON.stringify(e.detail).slice(0, 110)}</td></tr>
              ))}
            </tbody></table>
          )}
        </Card>
      </div>
    </>
  );
}
