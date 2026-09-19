"use client";

import Link from "next/link";
import { Json, fmt } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { Card, ErrorNote, Pill, ServiceState } from "@/components/ui";

export default function Services() {
  const { data, error } = usePoll<Json>("/api/overview", 4000);
  const services: Json[] = data?.services ?? [];
  return (
    <>
      <h1>Services</h1>
      <p className="sub">Registry of the monitored workload and its infrastructure, with live telemetry status.</p>
      <ErrorNote error={error} />
      <Card>
        <table>
          <thead>
            <tr><th>Service</th><th>Kind</th><th>Technology</th><th>Container</th><th>Status</th><th className="num">Events</th><th>Last seen</th></tr>
          </thead>
          <tbody>
            {services.map((s) => (
              <tr key={s.name} className="click">
                <td><Link href={`/services/${s.name}`}><b>{s.name}</b></Link></td>
                <td><Pill>{s.kind}</Pill></td>
                <td className="note">{s.technology}</td>
                <td className="mono">{s.container}</td>
                <td><ServiceState status={s.status} modes={s.modes} /></td>
                <td className="num">{s.total_events || "–"}</td>
                <td className="note">{fmt.ago(s.last_seen)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
