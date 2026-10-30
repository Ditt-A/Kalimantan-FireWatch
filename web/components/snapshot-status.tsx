"use client";

import { useEffect, useState } from "react";
import { api, type MetadataStatus } from "@/lib/api";

export function SnapshotStatus() {
  const [status, setStatus] = useState<MetadataStatus | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const refresh = () => api<MetadataStatus>("/metadata/status").then(setStatus).catch(() => setError(true));
    void refresh(); const timer = setInterval(() => void refresh(), 60_000);
    return () => clearInterval(timer);
  }, []);
  return (
    <aside className="snapshot-card">
      <div className="snapshot-top"><span className="pulse-dot" /><p>DATA STATUS</p></div>
      {status ? <>
        <h2>{status.layers.forecast.status === "current" ? "Current forecast" : "Historical forecast"}</h2>
        <dl>
          <div><dt>Forecast through</dt><dd>{status.prediction_snapshot.week_start_max}</dd></div>
          <div><dt>FIRMS through</dt><dd>{status.firms_snapshot.date_max}</dd></div>
          <div><dt>Forecast status</dt><dd>{status.layers.forecast.status}</dd></div>
          <div><dt>FIRMS status</dt><dd>{status.layers.firms.status}</dd></div>
          <div><dt>Local cases</dt><dd>{status.evidence.local_response_cases}</dd></div>
        </dl>
        <p className="muted">Snapshot data. Weather and fleet availability require operational verification.</p>
      </> : <>
        <h2>{error ? "API unavailable" : "Checking snapshot…"}</h2>
        <p className="muted">Start the Python API to read validation and freshness details.</p>
      </>}
    </aside>
  );
}
