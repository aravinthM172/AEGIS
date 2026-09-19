"use client";

import { ReactNode } from "react";

type Tone = "green" | "amber" | "red" | "blue" | "violet" | "grey";

export function Pill({ tone = "grey", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function Dot({ tone }: { tone: "green" | "amber" | "red" | "grey" }) {
  return <span className={`dot ${tone === "grey" ? "" : tone}`} />;
}

export function Card({ title, right, children, tight }: { title?: ReactNode; right?: ReactNode; children: ReactNode; tight?: boolean }) {
  return (
    <div className={`card${tight ? " tight" : ""}`}>
      {(title || right) && (
        <div className="row spread" style={{ marginBottom: 10 }}>
          {title ? <h2 style={{ margin: 0 }}>{title}</h2> : <span />}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Bar({ value, max = 100, tone }: { value: number | null | undefined; max?: number; tone?: Tone }) {
  const v = value ?? 0;
  const pct = Math.max(0, Math.min(100, (v / max) * 100));
  const color = tone
    ? `var(--${tone === "blue" ? "accent" : tone})`
    : v >= 60 ? "var(--red)" : v >= 20 ? "var(--amber)" : "var(--green)";
  return (
    <div className="bar">
      <i style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

export function ScoreBar({ score }: { score: number | null | undefined }) {
  const s = score ?? 0;
  const color = s >= 80 ? "var(--green)" : s >= 40 ? "var(--amber)" : "var(--red)";
  return (
    <div className="row" style={{ gap: 8 }}>
      <div className="bar" style={{ width: 90 }}>
        <i style={{ width: `${Math.max(0, Math.min(100, s))}%`, background: color }} />
      </div>
      <span className="mono">{score === null || score === undefined ? "–" : score.toFixed(1)}</span>
    </div>
  );
}

const EXPERIMENT_TONE: Record<string, Tone> = {
  COMPLETED: "green", RUNNING: "blue", PENDING: "blue", ABORTED: "amber", FAILED: "red", ROLLBACK_FAILED: "red",
};
export const ExperimentStatus = ({ status }: { status: string }) => <Pill tone={EXPERIMENT_TONE[status] || "grey"}>{status}</Pill>;

const ACTION_TONE: Record<string, Tone> = {
  VERIFIED: "green", PLANNED: "blue", RUNNING: "blue", PENDING_APPROVAL: "amber", DENIED: "red", UNSUPPORTED: "grey",
  FAILED: "red", VERIFICATION_FAILED: "red", REJECTED: "grey",
};
export const ActionStatus = ({ status }: { status: string }) => <Pill tone={ACTION_TONE[status] || "grey"}>{status}</Pill>;

export const LevelPill = ({ level }: { level: string | null | undefined }) =>
  level ? <Pill tone={level === "HIGH" ? "green" : level === "MEDIUM" ? "amber" : "red"}>{level}</Pill> : <Pill>n/a</Pill>;

export const ConfidencePill = ({ value }: { value: string | null | undefined }) =>
  value ? <Pill tone={value === "high" ? "green" : value === "medium" ? "amber" : "grey"}>{value} confidence</Pill> : null;

const SERVICE_TONE: Record<string, "green" | "amber" | "red" | "grey"> = {
  normal: "green", anomalous: "red", insufficient_data: "amber", no_telemetry: "grey", silent: "red",
};
export function ServiceState({ status, modes }: { status: string; modes?: string[] }) {
  const label = status === "insufficient_data" ? "idle / no traffic" : status === "no_telemetry" ? "not instrumented" : status;
  return (
    <span>
      <Dot tone={SERVICE_TONE[status] || "grey"} />
      {label}
      {modes && modes.length > 0 ? <span className="note"> ({modes.join(", ")})</span> : null}
    </span>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="banner bad" style={{ marginBottom: 14 }}>{error}</div> : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="note" style={{ padding: "8px 0" }}>{children}</div>;
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <div className="kv">
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <div>{k}</div>
          <div>{v}</div>
        </div>
      ))}
    </div>
  );
}
