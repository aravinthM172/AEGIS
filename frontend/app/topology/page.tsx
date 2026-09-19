"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Json } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import Graph, { edgesFrom, legend, Overlay } from "@/components/Graph";
import { Card, ErrorNote, Pill } from "@/components/ui";

export default function Topology() {
  const [basis, setBasis] = useState("combined");
  const [experimentId, setExperimentId] = useState("");
  const topo = usePoll<Json>("/api/topology", 0);
  const observed = usePoll<Json>("/api/topology/observed?window_minutes=1440", 0);
  const overview = usePoll<Json>("/api/overview", 4000);
  const experiments = usePoll<Json>("/api/experiments?limit=60", 0);
  const selected = useMemo(
    () => (experiments.data?.experiments ?? []).find((e: Json) => e.id === experimentId), [experiments.data, experimentId]);

  const edges = useMemo(() => edgesFrom(topo.data, observed.data, basis), [topo.data, observed.data, basis]);
  const liveStatus = useMemo(
    () => Object.fromEntries((overview.data?.services ?? []).map((s: Json) => [s.name, s.status])), [overview.data]);

  const overlay: Overlay | undefined = useMemo(() => {
    const r = selected?.result;
    if (!r || r.status !== "analyzed") return undefined;
    const affected: Overlay["affected"] = {};
    (r.direct ?? []).forEach((n: string) => (affected![n] = "direct"));
    (r.indirect ?? []).forEach((n: string) => (affected![n] = "indirect"));
    const healthy = Object.entries(r.services ?? {}).filter(([n, s]: [string, Json]) => s.status === "unaffected" && s.role !== "client" && n !== selected.target).map(([n]) => n);
    return { target: selected.target, affected, healthy, propagation: (r.propagation?.edges ?? []).map((e: Json) => ({ source: e.source, target: e.target })) };
  }, [selected]);

  return (
    <>
      <h1>Topology</h1>
      <p className="sub">Dependency graph. An arrow means “depends on”. Dashed = declared only; blue = seen calling in telemetry; red = failing or on the measured propagation path.</p>
      <ErrorNote error={topo.error || observed.error} />
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <label className="row">Edges
          <select value={basis} onChange={(e) => setBasis(e.target.value)}>
            <option value="combined">declared + observed</option>
            <option value="declared">declared only</option>
            <option value="observed">observed only (last 24h)</option>
          </select>
        </label>
        <label className="row">Overlay experiment
          <select value={experimentId} onChange={(e) => setExperimentId(e.target.value)} style={{ maxWidth: 360 }}>
            <option value="">— live status —</option>
            {(experiments.data?.experiments ?? []).filter((e: Json) => e.result?.status === "analyzed" && !e.dry_run).map((e: Json) => (
              <option key={e.id} value={e.id}>{e.fault_type} on {e.target} · {new Date(e.timeline.created_at).toLocaleTimeString()}</option>
            ))}
          </select>
        </label>
        {selected && <Link href={`/experiments/${selected.id}`}>open experiment →</Link>}
      </div>
      <Card>
        {topo.data ? <Graph nodes={topo.data.nodes} edges={edges} liveStatus={liveStatus} overlay={overlay} /> : <div className="note">Loading topology…</div>}
        <div className="row wrap" style={{ marginTop: 10 }}>
          {legend(!!overlay).map(([c, l]) => (
            <span key={l} className="note"><span className="dot" style={{ background: c }} />{l}</span>
          ))}
          {overlay && selected?.result?.propagation?.paths?.map((p: string) => <Pill key={p} tone="red">{p}</Pill>)}
        </div>
      </Card>
    </>
  );
}
