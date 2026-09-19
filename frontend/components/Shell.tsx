"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { API_URL, getApiKey, setApiKey } from "@/lib/api";

const NAV: [string, string][] = [
  ["/", "Dashboard"],
  ["/services", "Services"],
  ["/topology", "Topology"],
  ["/experiments", "Experiments"],
  ["/resilience", "Resilience"],
  ["/predictions", "Predictions"],
  ["/incidents", "Incidents"],
  ["/analyst", "AI Analyst"],
  ["/actions", "Actions"],
  ["/knowledge", "Knowledge"],
  ["/logs", "System Logs"],
];

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [key, setKey] = useState("");
  useEffect(() => setKey(getApiKey()), []);

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          FaultScope
          <small>Failure intelligence &amp; resilience</small>
        </div>
        <nav className="nav">
          {NAV.map(([href, label]) => (
            <Link key={href} href={href} className={href === "/" ? (path === "/" ? "active" : "") : path.startsWith(href) ? "active" : ""}>
              {label}
            </Link>
          ))}
        </nav>
        <div className="keybox">
          <label htmlFor="apikey">API key (needed to run experiments, actions, analysis)</label>
          <input
            id="apikey"
            type="password"
            value={key}
            placeholder="paste key"
            onChange={(e) => {
              setKey(e.target.value);
              setApiKey(e.target.value);
            }}
            style={{ width: "100%" }}
          />
          <div className="note" style={{ marginTop: 6 }}>Stored in this browser only. API: {API_URL}</div>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
