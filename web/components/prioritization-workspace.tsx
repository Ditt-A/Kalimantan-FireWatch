"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Alert, type AlertDisplayGroupsResponse, type FireStationLandmark, type FireStationLandmarks, type PriorityCell, type PriorityResponse, type ResponseCandidate, type ResponseCandidates, type ResponseRoute } from "@/lib/api";
import { OperationalMap, PriorityLegend } from "@/components/operational-map";

const priorityOrder: Record<string, number> = { Critical: 3, High: 2, Watch: 1, Low: 0 };

export function PrioritizationWorkspace() {
  const [cells, setCells] = useState<PriorityCell[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [selected, setSelected] = useState<Alert | null>(null);
  const [selectedCell, setSelectedCell] = useState<PriorityCell | null>(null);
  const [forecastWeek, setForecastWeek] = useState("");
  const [firmsDate, setFirmsDate] = useState("");
  const [firmsTotalDetections, setFirmsTotalDetections] = useState(0);
  const [prioritySummary, setPrioritySummary] = useState({ Critical: 0, High: 0, Watch: 0, Low: 0 });
  const [availableProvinces, setAvailableProvinces] = useState<string[]>([]);
  const [mapScope, setMapScope] = useState<"priority" | "peat" | "mineral">("priority");
  const [province, setProvince] = useState("all");
  const [priorityLevel, setPriorityLevel] = useState("all");
  const [minimumFootprintHa, setMinimumFootprintHa] = useState(100);
  const [mergeRadiusKm, setMergeRadiusKm] = useState(3);
  const [fireStationLandmarks, setFireStationLandmarks] = useState<FireStationLandmark[]>([]);
  const [responseCandidates, setResponseCandidates] = useState<ResponseCandidates | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<ResponseCandidate | null>(null);
  const [responseRoute, setResponseRoute] = useState<ResponseRoute | null>(null);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [candidateError, setCandidateError] = useState("");
  const [groundRadiusKm, setGroundRadiusKm] = useState(100);
  const [transportMode, setTransportMode] = useState<"ground" | "helicopter">("ground");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  const routeRequestSequence = useRef(0);
  const clearResponseSelection = () => { routeRequestSequence.current += 1; setResponseCandidates(null); setSelectedCandidate(null); setResponseRoute(null); setCandidateError(""); };
  const handleAlert = useCallback((alert: Alert) => { setSelectedCell(null); setSelected(alert); clearResponseSelection(); }, []);
  const handleCell = useCallback((cell: PriorityCell) => { setSelected(null); setSelectedCell(cell); clearResponseSelection(); }, []);
  const changeProvince = (value: string) => { setCells([]); setAlerts([]); setSelected(null); setSelectedCell(null); clearResponseSelection(); setProvince(value); };
  const changeMapScope = (value: "priority" | "peat" | "mineral") => { setCells([]); setAlerts([]); setSelected(null); setSelectedCell(null); clearResponseSelection(); setMapScope(value); };
  const changePriorityLevel = (value: string) => { setCells([]); setAlerts([]); setSelected(null); setSelectedCell(null); clearResponseSelection(); setPriorityLevel(value); };
  const changeMinimumFootprint = (value: number) => { setAlerts([]); setSelected(null); clearResponseSelection(); setMinimumFootprintHa(value); };
  const changeMergeRadius = (value: number) => { setAlerts([]); setSelected(null); clearResponseSelection(); setMergeRadiusKm(value); };

  const load = useCallback(async () => {
    const requestId = ++requestSequence.current;
    setLoading(true); setError("");
    const provinceQuery = province === "all" ? "" : `&province=${encodeURIComponent(province)}`;
    const scopeQuery = mapScope === "peat" ? "&peat_only=true" : mapScope === "mineral" ? "&mineral_only=true" : "";
    const priorityQuery = priorityLevel === "all" ? "" : `&priority_category=${encodeURIComponent(priorityLevel)}`;
    try {
      const [priority, firms] = await Promise.all([
        api<PriorityResponse>(`/priority/grid?limit=5000${provinceQuery}${scopeQuery}${priorityQuery}`),
        api<AlertDisplayGroupsResponse>(`/alert-display-groups?minimum_footprint_ha=${minimumFootprintHa}&merge_radius_km=${mergeRadiusKm}${provinceQuery}`),
      ]);
      if (requestId !== requestSequence.current) return;
      setCells(priority.items); setAlerts(firms.items); setForecastWeek(priority.snapshot_week); setFirmsDate(firms.snapshot_date); setFirmsTotalDetections(firms.total_detections); setPrioritySummary(priority.priority_summary);
      setAvailableProvinces((current) => Array.from(new Set([...current, ...priority.items.map((item) => item.province)])).sort());
    } catch (err) { if (requestId === requestSequence.current) setError(err instanceof Error ? err.message : "Unable to load dashboard data."); }
    finally { if (requestId === requestSequence.current) setLoading(false); }
  }, [province, mapScope, priorityLevel, minimumFootprintHa, mergeRadiusKm]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    let active = true;
    api<FireStationLandmarks>("/response-landmarks")
      .then((data) => { if (active) setFireStationLandmarks(data.items); })
      .catch(() => { if (active) setFireStationLandmarks([]); });
    return () => { active = false; };
  }, []);
  const activeIncident = useMemo(() => selected ? {
    key: `alert-${selected.detection_id}`, latitude: selected.latitude, longitude: selected.longitude,
    label: `FIRMS detection #${selected.detection_id}`,
  } : selectedCell ? {
    key: `grid-${selectedCell.grid_id}`, latitude: selectedCell.centroid_lat, longitude: selectedCell.centroid_lon,
    label: `${selectedCell.grid_id} forecast grid`,
  } : null, [selected, selectedCell]);
  useEffect(() => {
    if (!activeIncident) { setCandidateLoading(false); return; }
    let active = true;
    routeRequestSequence.current += 1;
    setCandidateLoading(true); setCandidateError(""); setResponseCandidates(null); setSelectedCandidate(null); setResponseRoute(null);
    api<ResponseCandidates>(`/response-candidates?latitude=${encodeURIComponent(activeIncident.latitude)}&longitude=${encodeURIComponent(activeIncident.longitude)}&ground_radius_km=${groundRadiusKm}`)
      .then((data) => { if (active) setResponseCandidates(data); })
      .catch((err) => { if (active) setCandidateError(err instanceof Error ? err.message : "Response locations could not be loaded."); })
      .finally(() => { if (active) setCandidateLoading(false); });
    return () => { active = false; };
  }, [activeIncident, groundRadiusKm]);
  const requestRoute = useCallback((candidate: ResponseCandidate, mode: "ground" | "helicopter") => {
    if (!activeIncident) return;
    const requestId = ++routeRequestSequence.current;
    setResponseRoute(null); setCandidateError("");
    api<ResponseRoute>(`/response-route?candidate_id=${encodeURIComponent(candidate.candidate_id)}&latitude=${encodeURIComponent(activeIncident.latitude)}&longitude=${encodeURIComponent(activeIncident.longitude)}&transport_mode=${mode}`)
      .then((route) => { if (requestId === routeRequestSequence.current) setResponseRoute(route); })
      .catch((err) => { if (requestId === routeRequestSequence.current) setCandidateError(err instanceof Error ? err.message : "Route could not be loaded."); });
  }, [activeIncident]);
  const handleCandidate = useCallback((candidate: ResponseCandidate) => { setSelectedCandidate(candidate); requestRoute(candidate, transportMode); }, [requestRoute, transportMode]);
  const changeTransportMode = useCallback((mode: "ground" | "helicopter") => { setTransportMode(mode); if (selectedCandidate) requestRoute(selectedCandidate, mode); }, [requestRoute, selectedCandidate]);
  const queue = useMemo(() => [...cells].sort((a, b) => priorityOrder[b.priority_category] - priorityOrder[a.priority_category] || b.predicted_fire_probability - a.predicted_fire_probability).slice(0, 10), [cells]);
  const mapCells = useMemo(() => {
    if (mapScope !== "priority" || priorityLevel !== "all") return cells;
    const overview = cells.filter((cell) => cell.priority_category === "Critical" || cell.priority_category === "High");
    return selectedCell && !overview.some((cell) => cell.grid_id === selectedCell.grid_id) ? [...overview, selectedCell] : overview;
  }, [cells, mapScope, priorityLevel, selectedCell]);
  const critical = prioritySummary.Critical;
  const highOrCritical = prioritySummary.Critical + prioritySummary.High;

  return <main className="page workspace stack-lg">
    <section className="page-heading"><div><p className="eyebrow">FEATURE 01 · VALIDATED SNAPSHOT</p><h1>Prioritization & localization</h1><p>Forecast grids indicate monitoring priority; red points are raw NASA FIRMS satellite detections.</p></div><button className="button button-quiet" onClick={() => void load()}>{loading ? "Refreshing…" : "Refresh snapshot"}</button></section>
    <section className="filterbar"><label>Province<select value={province} onChange={(event) => changeProvince(event.target.value)}><option value="all">All provinces</option>{availableProvinces.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><label>Map layer<select value={mapScope} onChange={(event) => changeMapScope(event.target.value as "priority" | "peat" | "mineral")}><option value="priority">All priority grids</option><option value="peat">Peat context (BIG / ≥10%)</option><option value="mineral">Mineral reference (SoilGrids)</option></select></label><label>Priority level<select value={priorityLevel} onChange={(event) => changePriorityLevel(event.target.value)}><option value="all">All levels</option><option value="Critical">Critical</option><option value="High">High</option><option value="Watch">Watch</option><option value="Low">Low</option></select></label><label>Min marker footprint<select value={minimumFootprintHa} onChange={(event) => changeMinimumFootprint(Number(event.target.value))}><option value={0}>No minimum</option><option value={25}>25 ha proxy</option><option value={100}>100 ha proxy</option><option value={250}>250 ha proxy</option></select></label><label>Merge radius<select value={mergeRadiusKm} onChange={(event) => changeMergeRadius(Number(event.target.value))}><option value={1}>1 km</option><option value={3}>3 km</option><option value={5}>5 km</option><option value={10}>10 km</option></select></label><div className="filter-note">Forecast: {forecastWeek || "loading…"} · FIRMS: {firmsDate || "loading…"} · Grouping: ≥{minimumFootprintHa} ha / {mergeRadiusKm} km</div></section>
    {error && <p className="error-banner">{error}</p>}
    <section className="metric-grid"><Metric label="Critical grids" value={critical.toLocaleString()} detail="Full selected snapshot" /><Metric label="High + critical" value={highOrCritical.toLocaleString()} detail="Full selected snapshot" /><Metric label="FIRMS detections" value={firmsTotalDetections.toLocaleString()} detail={`${alerts.length.toLocaleString()} grouped map markers`} /><Metric label="Priority cells shown" value={cells.length.toLocaleString()} detail={`Top ranked of ${critical + prioritySummary.High + prioritySummary.Watch + prioritySummary.Low}`} /></section>
    <section className="map-layout"><div className="map-card"><div className="map-header"><div><h2>Operations map</h2><p>{mapScope === "peat" ? "Grid centre mapped by BIG, or grid overlap with BIG peat is at least 10%. Click a grid to load its peat context in the planner." : mapScope === "mineral" ? "Blue cells have SoilGrids Histosols probability of 20% or less and no BIG peat override. This is a map reference, not a field mineral observation." : priorityLevel === "all" ? "FIRMS markers follow the active minimum footprint and kilometre merge radius. Green markers are mapped fire stations; use the priority-level filter to inspect Watch or Low grids." : "Change the FIRMS merge settings or select a detection group or forecast grid."}</p></div><PriorityLegend scope={mapScope} showResponseLocations={false} /></div><OperationalMap key={`firms-${minimumFootprintHa}-${mergeRadiusKm}-${alerts.length}`} cells={mapCells} alerts={mapScope === "priority" ? alerts : []} scope={mapScope} fireStationLandmarks={fireStationLandmarks} responseCandidates={responseCandidates ? responseCandidates.ground_candidates : []} selectedAlertId={selected?.detection_id ?? null} dimAlerts={Boolean(activeIncident)} selectedCandidateId={selectedCandidate?.candidate_id ?? null} routeCoordinates={responseRoute?.route.coordinates ?? []} onAlertClick={handleAlert} onCellClick={handleCell} onCandidateClick={handleCandidate} /></div><aside className="priority-queue"><div className="panel-heading"><div><p className="eyebrow">{mapScope === "peat" ? "PEAT CONTEXT QUEUE" : mapScope === "mineral" ? "MINERAL REFERENCE QUEUE" : "TOP QUEUE"}</p><h2>{mapScope === "peat" ? "Inspect peat context" : mapScope === "mineral" ? "Inspect mineral reference" : "Inspect priority"}</h2></div><span>{queue.length}</span></div><div className="queue-scroll">{queue.map((cell) => <button className="queue-item queue-button" onClick={() => handleCell(cell)} key={cell.grid_id}><div><span className={`priority-dot ${mapScope === "mineral" ? "mineral" : cell.priority_category.toLowerCase()}`} /><b>{mapScope === "mineral" ? "Mineral reference" : cell.priority_category}</b><p>{cell.district}, {cell.province}</p></div><div className="queue-score"><b>{mapScope === "peat" ? `${cell.peat_pct.toFixed(0)}%` : mapScope === "mineral" ? `${(cell.histosols_probability_pct ?? 0).toFixed(0)}%` : `${(cell.predicted_fire_probability * 100).toFixed(1)}%`}</b><small>{mapScope === "peat" ? "peat coverage" : mapScope === "mineral" ? "Histosols probability" : "probability"}</small></div></button>)}</div></aside></section>
    {activeIncident && <ResponseCandidatePanel incidentLabel={activeIncident.label} candidates={responseCandidates} selectedCandidate={selectedCandidate} route={responseRoute} loading={candidateLoading} error={candidateError} groundRadiusKm={groundRadiusKm} transportMode={transportMode} onGroundRadiusChange={setGroundRadiusKm} onTransportModeChange={changeTransportMode} onCandidateClick={handleCandidate} />}
    <section className="info-strip"><span className="info-icon">!</span><p><b>Interpretation:</b> FIRMS is a satellite active-fire detection, not a confirmed incident. A forecast score supports monitoring and validation; it does not trigger a dispatch.</p></section>
    {selected && <aside className="detail-drawer" role="dialog" aria-label="FIRMS alert detail"><button className="drawer-close" onClick={() => { setSelected(null); clearResponseSelection(); }} aria-label="Close alert detail">×</button><p className="eyebrow">SATELLITE ALERT · {selected.source_product}</p><h2>{(selected.detection_count ?? 1) > 1 ? `FIRMS group · ${selected.detection_count} detections` : `FIRMS detection #${selected.detection_id}`}</h2><dl className="detail-list"><div><dt>Acquired</dt><dd>{new Date(selected.acq_datetime).toLocaleString("id-ID")}</dd></div>{(selected.detection_count ?? 1) > 1 && <div><dt>Sensor-footprint proxy</dt><dd>{selected.footprint_ha_proxy_total?.toFixed(1)} ha · {selected.detection_count} detections</dd></div>}<div><dt>FRP</dt><dd>{selected.frp_min !== undefined ? `${selected.frp_min.toFixed(2)}–${selected.frp_max?.toFixed(2)} MW` : `${selected.frp.toFixed(2)} MW`}</dd></div><div><dt>Location</dt><dd>{selected.district}, {selected.province}</dd></div><div><dt>Coordinate</dt><dd>{selected.latitude.toFixed(5)}, {selected.longitude.toFixed(5)}</dd></div><div><dt>Sensor</dt><dd>{selected.satellite} · {selected.instrument} · {selected.daynight}</dd></div></dl><Link className="button button-primary full-width" href={`/response-planner?lat=${selected.latitude}&lon=${selected.longitude}&source=firms-alert&alertId=${selected.detection_id}&gridId=${selected.grid_id}&minFootprintHa=${minimumFootprintHa}&areaHa=${selected.footprint_ha_proxy_total ?? 0}&mergeRadiusKm=${mergeRadiusKm}&province=${encodeURIComponent(province === "all" ? "" : province)}`}>Load full map context in planner <span>→</span></Link><p className="muted small">Sensor footprint is not a field-verified burned perimeter. A field operator must verify this location, perimeter, access, and conditions.</p></aside>}
    {selectedCell && <aside className="detail-drawer" role="dialog" aria-label="Forecast grid detail"><button className="drawer-close" onClick={() => { setSelectedCell(null); clearResponseSelection(); }} aria-label="Close grid detail">×</button><p className="eyebrow">FORECAST GRID · {selectedCell.week_start.slice(0, 10)}</p><h2>{selectedCell.priority_category} monitoring priority</h2><dl className="detail-list"><div><dt>Location</dt><dd>{selectedCell.district}, {selectedCell.province}</dd></div><div><dt>Fire probability</dt><dd>{(selectedCell.predicted_fire_probability * 100).toFixed(1)}%</dd></div><div><dt>Expected hotspots</dt><dd>{selectedCell.predicted_hotspot_count_next_7d.toFixed(2)} / 7 days</dd></div><div><dt>Peat share</dt><dd>{selectedCell.peat_pct.toFixed(1)}%</dd></div>{mapScope === "mineral" && <div><dt>SoilGrids Histosols</dt><dd>{selectedCell.histosols_probability_pct?.toFixed(0)}%</dd></div>}<div><dt>Coordinate</dt><dd>{selectedCell.centroid_lat.toFixed(5)}, {selectedCell.centroid_lon.toFixed(5)}</dd></div></dl><Link className="button button-primary full-width" href={`/response-planner?lat=${selectedCell.centroid_lat}&lon=${selectedCell.centroid_lon}&source=forecast-grid&gridId=${selectedCell.grid_id}`}>Load full grid context in planner <span>→</span></Link><p className="muted small">A forecast grid is not a confirmed incident. Enter and verify the actual incident size in the planner.</p></aside>}
  </main>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) { return <article className="metric-card"><p>{label}</p><strong>{value}</strong><small>{detail}</small></article>; }

function ResponseCandidatePanel({ incidentLabel, candidates, selectedCandidate, route, loading, error, groundRadiusKm, transportMode, onGroundRadiusChange, onTransportModeChange, onCandidateClick }: {
  incidentLabel: string; candidates: ResponseCandidates | null; selectedCandidate: ResponseCandidate | null; route: ResponseRoute | null; loading: boolean; error: string; groundRadiusKm: number; transportMode: "ground" | "helicopter";
  onGroundRadiusChange: (value: number) => void; onTransportModeChange: (mode: "ground" | "helicopter") => void; onCandidateClick: (candidate: ResponseCandidate) => void;
}) {
  const items = candidates?.ground_candidates ?? [];
  return <section className="response-candidate-panel"><div className="response-candidate-heading"><div><p className="eyebrow">RESPONSE LOCATION CONTEXT</p><h2>Nearby fire stations</h2><p>{incidentLabel}. Select a fire station, then choose a road or helicopter route to the selected area.</p></div><div className="response-radius-controls"><label>Fire-station radius<select value={groundRadiusKm} onChange={(event) => onGroundRadiusChange(Number(event.target.value))}><option value={50}>50 km</option><option value={100}>100 km</option><option value={200}>200 km</option></select></label></div></div>
    {loading && <p className="muted small">Loading mapped fire stations…</p>}
    {error && <p className="error-banner">{error}</p>}
    {!loading && candidates && <div className="candidate-columns"><article><div className="candidate-section-heading"><span className="candidate-mode ground">Station</span><h3>Fire stations ({items.length})</h3></div><div className="candidate-list">{items.length === 0 ? <p className="muted small">No mapped fire station inside this radius.</p> : items.map((candidate) => <button className={`candidate-item ${candidate.candidate_id === selectedCandidate?.candidate_id ? "selected" : ""}`} key={candidate.candidate_id} onClick={() => onCandidateClick(candidate)}><span className="candidate-mode ground">Station</span><span><b>{candidate.name}</b><small>{candidate.district ?? candidate.province ?? "Kalimantan"} · {candidate.straight_line_distance_km.toFixed(1)} km straight-line</small></span><span className="candidate-arrow">→</span></button>)}</div></article><article className="transport-route-card"><div className="candidate-section-heading"><span className="candidate-mode air">Route</span><h3>From selected fire station</h3></div><div className="transport-mode-toggle"><button className={transportMode === "ground" ? "active" : ""} onClick={() => onTransportModeChange("ground")}>Road vehicle</button><button className={transportMode === "helicopter" ? "active" : ""} onClick={() => onTransportModeChange("helicopter")}>Helicopter direct</button></div><p className="muted small">{selectedCandidate ? "Choose a route mode to redraw the connection." : "Select a fire station first."}</p></article></div>}
    {route && <div className="route-summary"><b>{route.candidate.name}</b><span>{route.transport_mode === "ground" ? `Road route · ${route.route.distance_km.toFixed(1)} km${route.route.duration_min !== null ? ` · about ${Math.round(route.route.duration_min)} min` : ""}` : `Helicopter direct route · ${route.route.distance_km.toFixed(1)} km straight-line`}</span><small>{route.route.limitation}</small></div>}
    <p className="candidate-disclaimer">Locations are mapped OSM fire-station records. The helicopter line is a direct flight-path reference from the selected station, not a road route or a bandara connection. Confirm crew, aircraft, airspace, landing zone, and dispatch availability before mobilization.</p>
  </section>;
}
