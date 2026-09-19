"use client";

import { useMemo } from "react";
import { Json } from "@/lib/api";

export interface GraphNode { name: string; kind: string }
export interface GraphEdge { source: string; target: string; relation?: string; declared: boolean; observed: boolean; errorRate?: number; calls?: number }
export interface Overlay {
  target?: string;
  affected?: Record<string, "direct" | "indirect" | "target" | "unexplained">;
  healthy?: string[];
  propagation?: { source: string; target: string }[];
}

const W = 980;
const H = 470;

function layers(nodes: GraphNode[], edges: GraphEdge[]): Record<string, number> {
  const depth: Record<string, number> = Object.fromEntries(nodes.map((n) => [n.name, 0]));
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of edges) {
      if (depth[e.source] !== undefined && depth[e.target] !== undefined && depth[e.target] < depth[e.source] + 1) {
        depth[e.target] = depth[e.source] + 1;
        changed = true;
      }
    }
    if (!changed) break;
  }
  return depth;
}

export default function Graph({ nodes, edges, liveStatus, overlay }: {
  nodes: GraphNode[]; edges: GraphEdge[]; liveStatus?: Record<string, string>; overlay?: Overlay;
}) {
  const pos = useMemo(() => {
    const depth = layers(nodes, edges);
    const maxDepth = Math.max(0, ...Object.values(depth));
    const byLayer: Record<number, GraphNode[]> = {};
    nodes.forEach((n) => (byLayer[depth[n.name]] ||= []).push(n));
    const out: Record<string, { x: number; y: number }> = {};
    Object.entries(byLayer).forEach(([d, list]) => {
      list.sort((a, b) => a.name.localeCompare(b.name));
      list.forEach((n, i) => {
        out[n.name] = {
          x: ((i + 1) * W) / (list.length + 1),
          y: maxDepth === 0 ? H / 2 : 55 + (Number(d) * (H - 110)) / maxDepth,
        };
      });
    });
    return out;
  }, [nodes, edges]);

  const onPath = (e: GraphEdge) => overlay?.propagation?.some((p) => p.source === e.source && p.target === e.target);

  const nodeColor = (n: GraphNode) => {
    if (overlay) {
      if (n.name === overlay.target) return "#ff5d6c";
      const role = overlay.affected?.[n.name];
      if (role) return "#f5b84c";
      if (overlay.healthy?.includes(n.name)) return "#3ddc97";
      return "#3a4a5c";
    }
    const s = liveStatus?.[n.name];
    return s === "anomalous" ? "#ff5d6c" : s === "normal" ? "#3ddc97" : s === "insufficient_data" ? "#f5b84c" : "#3a4a5c";
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", background: "#0d141c", borderRadius: 10, border: "1px solid var(--border)" }}>
      <defs>
        {[["a-decl", "#5b6f84"], ["a-obs", "#4cc2ff"], ["a-bad", "#ff5d6c"]].map(([id, color]) => (
          <marker key={id} id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={color} />
          </marker>
        ))}
      </defs>
      {edges.map((e, i) => {
        const a = pos[e.source], b = pos[e.target];
        if (!a || !b) return null;
        const bad = onPath(e) || (e.errorRate ?? 0) > 0.2;
        const color = bad ? "#ff5d6c" : e.observed ? "#4cc2ff" : "#5b6f84";
        const marker = bad ? "a-bad" : e.observed ? "a-obs" : "a-decl";
        const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
        const x1 = a.x + (dx / len) * 26, y1 = a.y + (dy / len) * 26, x2 = b.x - (dx / len) * 30, y2 = b.y - (dy / len) * 30;
        return (
          <g key={i}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={bad ? 2.6 : 1.5}
              strokeDasharray={e.declared && !e.observed ? "5 4" : undefined} markerEnd={`url(#${marker})`} opacity={overlay && !onPath(e) ? 0.35 : 0.95} />
            <title>{`${e.source} → ${e.target}${e.relation ? ` (${e.relation})` : ""}${e.calls ? ` · ${e.calls} calls, ${Math.round((e.errorRate ?? 0) * 100)}% errors` : ""}`}</title>
          </g>
        );
      })}
      {nodes.map((n) => {
        const p = pos[n.name];
        if (!p) return null;
        const infra = n.kind !== "service";
        return (
          <g key={n.name} transform={`translate(${p.x},${p.y})`}>
            {infra ? (
              <rect x={-52} y={-22} width={104} height={44} rx={9} fill="#111821" stroke={nodeColor(n)} strokeWidth={3} />
            ) : (
              <ellipse rx={58} ry={24} fill="#111821" stroke={nodeColor(n)} strokeWidth={3} />
            )}
            <text textAnchor="middle" y={-2} style={{ fontWeight: 600 }}>{n.name}</text>
            <text textAnchor="middle" y={13} style={{ fill: "#8195a8", fontSize: 10.5 }}>
              {overlay?.affected?.[n.name] ?? n.kind}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function legend(overlay: boolean): [string, string][] {
  return overlay
    ? [["#ff5d6c", "fault target"], ["#f5b84c", "measured affected"], ["#3ddc97", "measured unaffected"], ["#3a4a5c", "not measurable"]]
    : [["#3ddc97", "normal"], ["#ff5d6c", "anomalous"], ["#f5b84c", "idle / no traffic"], ["#3a4a5c", "not instrumented"]];
}

export function edgesFrom(topology: Json, observed: Json | null, basis: string): GraphEdge[] {
  const map = new Map<string, GraphEdge>();
  if (basis !== "observed") {
    (topology?.edges ?? []).forEach((e: Json) =>
      map.set(`${e.source}>${e.target}`, { source: e.source, target: e.target, relation: e.relation, declared: true, observed: false }));
  }
  if (basis !== "declared") {
    (observed?.edges ?? []).forEach((o: Json) => {
      const key = `${o.source}>${o.target}`;
      const existing = map.get(key);
      const merged = { source: o.source, target: o.target, relation: existing?.relation ?? o.relation, declared: !!existing, observed: true, errorRate: o.error_rate, calls: o.calls };
      map.set(key, merged);
    });
  }
  return [...map.values()];
}
