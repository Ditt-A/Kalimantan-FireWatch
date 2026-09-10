"use client";

import { useEffect, useState } from "react";
import { api, type MetadataStatus } from "@/lib/api";

export function SnapshotStatus() {
  const [status, setStatus] = useState<MetadataStatus | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    api<MetadataStatus>("/metadata/status").then(setStatus).catch(() => setError(true));
  }, []);
  return (
    <aside className="snapshot-card">
      <div className="snapshot-top"><span className="pulse-dot" /><p>DATA STATUS</p></div>
      {status ? <>
        <h2>Validated snapshot</h2>
        <dl>
          <div><dt>Forecast through</dt><dd>{status.prediction_snapshot.week_start_max}</dd></div>
          <div><dt>FIRMS through</dt><dd>{status.firms_snapshot.date_max}</dd></div>
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
