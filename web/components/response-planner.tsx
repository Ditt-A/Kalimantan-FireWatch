"use client";

import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, type PlannerContext, type ResponsePlan } from "@/lib/api";

type FormState = { latitude: string; longitude: string; area_ha: string; soil_class: string; fire_type_class: string; fwi_class: string; field_water_note: string };
const defaultForm: FormState = { latitude: "-2.2100", longitude: "113.9100", area_ha: "", soil_class: "unknown", fire_type_class: "unknown", fwi_class: "unknown", field_water_note: "" };
const normaliseCoordinate = (value: string) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? String(Math.round(numeric * 100_000) / 100_000) : value;
};

export function ResponsePlanner() {
  const params = useSearchParams();
  const [form, setForm] = useState<FormState>(defaultForm);
  const [plan, setPlan] = useState<ResponsePlan | null>(null);
  const [mapContext, setMapContext] = useState<PlannerContext | null>(null);
  const [contextError, setContextError] = useState("");
  const [contextLoading, setContextLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const mapLatitude = params.get("lat");
  const mapLongitude = params.get("lon");
  const mapGridId = params.get("gridId");
  const mapAlertId = params.get("alertId");
  const mapMinimumFootprintHa = params.get("minFootprintHa");
  const mapAreaHa = params.get("areaHa");
  const mapMergeRadiusKm = params.get("mergeRadiusKm");
  const mapProvince = params.get("province");
  const validMapAlertId = mapAlertId && Number.isInteger(Number(mapAlertId)) && Number(mapAlertId) > 0 ? Number(mapAlertId) : null;
  const validMapMinimumFootprintHa = mapMinimumFootprintHa && Number.isFinite(Number(mapMinimumFootprintHa)) && Number(mapMinimumFootprintHa) >= 0 ? Number(mapMinimumFootprintHa) : null;
  const validMapAreaHa = mapAreaHa && Number.isFinite(Number(mapAreaHa)) && Number(mapAreaHa) > 0 ? Number(mapAreaHa) : null;
  const validMapMergeRadiusKm = mapMergeRadiusKm && Number.isFinite(Number(mapMergeRadiusKm)) && Number(mapMergeRadiusKm) > 0 ? Number(mapMergeRadiusKm) : null;
  // A marker selection can change its grouped footprint while retaining the
  // same representative coordinate. Keep every selection-setting in this
  // token so form defaults cannot remain stale after a new map click.
  const mapSelectionKey = [mapLatitude, mapLongitude, mapGridId, mapAlertId, mapMinimumFootprintHa, mapAreaHa, mapMergeRadiusKm, mapProvince].join("|");
  useEffect(() => {
    if (!mapLatitude || !mapLongitude || !Number.isFinite(Number(mapLatitude)) || !Number.isFinite(Number(mapLongitude))) return;
    // A new map selection is a new incident scenario. Reset prior map-derived
    // inputs before the new backend defaults arrive, preventing stale peat/FWI
    // values from a previously clicked grid from remaining in the form.
    // The link already carries the active marker group's footprint. Showing
    // it immediately avoids a blank required area field while the richer
    // planner-context request (peat, weather, water, and re-grouping) runs.
    // The authoritative backend default replaces this value below.
    setForm({
      ...defaultForm,
      latitude: normaliseCoordinate(mapLatitude),
      longitude: normaliseCoordinate(mapLongitude),
      area_ha: validMapAreaHa === null ? "" : String(validMapAreaHa),
    });
    setPlan(null); setError("");
  }, [mapSelectionKey, mapLatitude, mapLongitude, validMapAreaHa]);
  useEffect(() => {
    if (!mapLatitude || !mapLongitude || !Number.isFinite(Number(mapLatitude)) || !Number.isFinite(Number(mapLongitude))) {
      setMapContext(null);
      setContextLoading(false);
      return;
    }
    let active = true;
    setContextError(""); setMapContext(null); setContextLoading(true);
    const gridQuery = mapGridId ? `&grid_id=${encodeURIComponent(mapGridId)}` : "";
    const alertQuery = validMapAlertId !== null ? `&alert_id=${encodeURIComponent(String(validMapAlertId))}` : "";
    const minimumFootprintQuery = validMapMinimumFootprintHa !== null ? `&map_minimum_footprint_ha=${encodeURIComponent(String(validMapMinimumFootprintHa))}` : "";
    const areaQuery = validMapAreaHa !== null ? `&map_area_ha=${encodeURIComponent(String(validMapAreaHa))}` : "";
    const radiusQuery = validMapMergeRadiusKm !== null ? `&map_merge_radius_km=${encodeURIComponent(String(validMapMergeRadiusKm))}` : "";
    const provinceQuery = mapProvince ? `&map_province=${encodeURIComponent(mapProvince)}` : "";
    api<PlannerContext>(`/planner-context?latitude=${encodeURIComponent(mapLatitude)}&longitude=${encodeURIComponent(mapLongitude)}${gridQuery}${alertQuery}${minimumFootprintQuery}${areaQuery}${radiusQuery}${provinceQuery}`)
      .then((context) => {
        if (!active) return;
        setMapContext(context);
        const defaults = context.model_input_defaults;
        const resolvedAreaHa = Number(defaults.area_ha);
        setForm((current) => ({
          ...current,
          latitude: normaliseCoordinate(String(defaults.latitude)), longitude: normaliseCoordinate(String(defaults.longitude)),
          // For forecast-grid links the API supplies the documented-local
          // starting value; for FIRMS links it recomputes the current display
          // group's footprint. Never replace a valid visible map value with
          // an empty/invalid response.
          area_ha: Number.isFinite(resolvedAreaHa) && resolvedAreaHa > 0 ? String(resolvedAreaHa) : current.area_ha,
          soil_class: defaults.soil_class, fire_type_class: defaults.fire_type_class, fwi_class: defaults.fwi_class,
        }));
      })
      .catch((err) => { if (active) setContextError(err instanceof Error ? err.message : "Map context could not be loaded."); })
      .finally(() => { if (active) setContextLoading(false); });
    return () => { active = false; };
  }, [mapSelectionKey, mapLatitude, mapLongitude, mapGridId, validMapAlertId, validMapMinimumFootprintHa, validMapAreaHa, validMapMergeRadiusKm, mapProvince]);
  const update = (key: keyof FormState, value: string) => {
    if (key === "latitude" || key === "longitude") { setMapContext(null); setContextError(""); }
    setForm((current) => ({ ...current, [key]: value }));
  };
  async function submit(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError("");
    try { setPlan(await api<ResponsePlan>("/response-plans", { method: "POST", body: JSON.stringify({ ...form, grid_id: mapContext?.model_input_defaults.grid_id ?? null, latitude: Number(form.latitude), longitude: Number(form.longitude), area_ha: Number(form.area_ha), field_water_note: form.field_water_note || null }) })); }
    catch (err) { setError(err instanceof Error ? err.message : "The plan could not be generated."); }
    finally { setLoading(false); }
  }
  return <main className="page stack-lg">
    <section className="page-heading"><div><p className="eyebrow">FEATURE 02 · RESPONSE PLANNING</p><h1>Response planning calculator</h1><p>Combines personnel planning, a six-person vehicle-capacity policy, local evidence, and spatial water context.</p></div><span className="status-pill warning"><i />Operator approval required</span></section>
    {params.get("lat") && params.get("lon") && <section className="map-prefill">✓ Coordinate loaded from {params.get("source") === "forecast-grid" ? "a forecast grid" : "a FIRMS map alert"}. Verify it against the real incident location before submitting.</section>}
    {contextLoading && <p className="muted small">Loading grid, peat, mineral-reference, local FWI, fire-type, hotspot, and water-access context…</p>}
    {mapContext && <MapContextSummary context={mapContext} />}
    {mapContext && <section className="field-source-note"><b>Context autofill:</b> Soil condition is set to <b>{mapContext.soil_context.recommended_soil_class === "peat" ? "Peat" : mapContext.soil_context.recommended_soil_class === "mineral" ? "Mineral reference" : "Not yet confirmed"}</b>. Fire-weather FWI is calculated locally from Open-Meteo weather. Fire type is only filled when a saved operator observation exists for this grid; confirm all values against the current incident.</section>}
    {contextError && <p className="error-banner">Map context unavailable: {contextError}</p>}
    <section className="planner-layout"><form className="planner-form stack-md" onSubmit={submit}><div className="panel-heading"><div><p className="eyebrow">SCENARIO INPUT</p><h2>Describe the field situation</h2></div></div><p className="muted">FIRMS provides a detection point, not a burned perimeter. Enter a field- or perimeter-derived area; the planning reference supports 0.01–10,000 ha.</p><div className="field-grid"><Field label="Latitude"><input required type="number" step="any" min="-4.5" max="4.5" value={form.latitude} onChange={(e) => update("latitude", e.target.value)} /></Field><Field label="Longitude"><input required type="number" step="any" min="108" max="119.5" value={form.longitude} onChange={(e) => update("longitude", e.target.value)} /></Field></div><Field label="Estimated incident area (ha)"><input required type="number" min="0.01" max="10000" step="any" placeholder="Example: 2" value={form.area_ha} onChange={(e) => update("area_ha", e.target.value)} />{mapContext?.model_input_defaults.area_ha != null && <small className="field-help">Initial map value: {mapContext.model_input_defaults.sources.area_ha}</small>}</Field><Field label="Soil condition"><Select value={form.soil_class} onChange={(v) => update("soil_class", v)} options={[["unknown", "Not yet confirmed"], ["peat", "Peat"], ["mineral", "Mineral"]]} /></Field><Field label="Observed fire type"><Select value={form.fire_type_class} onChange={(v) => update("fire_type_class", v)} options={[["unknown", "Not yet confirmed"], ["surface", "Surface"], ["ground_smouldering", "Ground / smouldering"], ["mixed", "Mixed"], ["crown", "Crown"]]} /></Field><Field label="Fire-weather FWI context"><Select value={form.fwi_class} onChange={(v) => update("fwi_class", v)} options={[["unknown", "Not supplied"], ["low", "Low"], ["moderate", "Moderate"], ["high", "High"], ["very_high", "Very high"]]} /></Field><Field label="Field water note (optional)"><textarea value={form.field_water_note} placeholder="Example: canal at 800 m, access not yet confirmed" onChange={(e) => update("field_water_note", e.target.value)} /></Field><button disabled={loading} className="button button-primary full-width" type="submit">{loading ? "Calculating…" : "Generate planning reference"} <span>→</span></button>{error && <p className="error-banner">{error}</p>}</form><PlanResult plan={plan} /></section>
    <section className="info-strip"><span className="info-icon">!</span><p><b>Non-dispatch safeguard:</b> this result does not assign people, trucks, water, routes, or command authority. Verify perimeter, water, access, roster, and fleet availability before mobilisation.</p></section>
  </main>;
}

function PlanResult({ plan }: { plan: ResponsePlan | null }) {
  if (!plan) return <aside className="planner-result empty-result"><p className="eyebrow">OUTPUT</p><h2>Awaiting a scenario</h2><p>Enter a coordinate and estimated area to generate a reproducible planning reference.</p><div className="empty-bars"><i /><i /><i /></div></aside>;
  const output = plan.planning_output;
  return <aside className="planner-result stack-lg"><div className="result-warning"><span>!</span><p>Planning reference only · automatic dispatch disabled</p></div><div><p className="eyebrow">RECOMMENDED PLANNING CAPACITY</p><div className="result-cards"><article><strong>{output.planned_personnel}</strong><span>Personnel</span></article><article><strong>{output.ground_response_fire_engine_capacity}</strong><span>Fire-engine capacity</span></article></div><p className="muted small">Vehicle capacity uses <code>{output.fire_engine_formula}</code>; it is not an availability or deployment order.</p></div><div className="context-card"><p className="eyebrow">SPATIAL CONTEXT</p><dl><div><dt>Coverage grid</dt><dd>{plan.spatial_context.grid_id}</dd></div><div><dt>Water access</dt><dd>{plan.spatial_context.water_access_class} · index {plan.spatial_context.water_access_index_relative_0_100.toFixed(1)}</dd></div><div><dt>Nearest permanent water</dt><dd>{plan.spatial_context.nearest_permanent_surface_water_km.toFixed(2)} km</dd></div><div><dt>Complexity</dt><dd>{output.complexity_level.replaceAll("_", " ")}</dd></div></dl></div><div><p className="eyebrow">CONDITIONS & VERIFICATION</p><ul className="checklist">{[...plan.applied_conditions, ...plan.requirements].map((text) => <li key={text}>{text}</li>)}</ul></div><div><p className="eyebrow">CLOSEST LOCAL EVIDENCE</p><div className="evidence-mini-list">{plan.evidence_cases.map((item) => <article key={item.incident_id}><div><b>{item.area_ha} ha · {item.soil_type ?? "soil not reported"}</b><p>{item.personnel_total ?? "—"} personnel · {item.documented_ground_response_vehicle_total ?? "—"} documented vehicles</p></div><a href={item.source_document_url_retrieved} target="_blank">p. {item.source_page}</a></article>)}</div></div></aside>;
}

function MapContextSummary({ context }: { context: PlannerContext }) {
  const forecast = context.spatial_context.forecast_grid;
  const peat = context.peat_context;
  const soil = context.soil_context;
  const weather = context.fire_weather_context;
  const fireType = context.fire_type_context;
  return <section className="loaded-context" aria-label="Map context loaded into planner">
    <div><p className="eyebrow">LOADED FROM MAP</p><h2>{context.spatial_context.grid_id} · {context.spatial_context.district}</h2><p className="muted small">Validated snapshot context. It enriches the plan but does not confirm field conditions.</p></div>
    <div className="loaded-context-grid">
      <article><span>Forecast priority</span><b>{forecast?.priority_category ?? "Unavailable"}</b><small>{forecast ? `${(forecast.predicted_fire_probability * 100).toFixed(1)}% fire probability` : ""}</small></article>
      <article><span>Peat context</span><b>{peat.mapped_as_peat ? "Mapped peat" : peat.recommended_soil_class === "peat" ? "Peat grid context" : "Not mapped as peat"}</b><small>{peat.peat_thick_class ? `Class: ${peat.peat_thick_class}` : peat.message}</small></article>
      <article><span>Mineral reference</span><b>{soil.recommended_soil_class === "mineral" ? "Indicative mineral" : soil.histosols_probability_pct === undefined ? "Unavailable" : `${soil.histosols_probability_pct.toFixed(0)}% Histosols`}</b><small>{soil.message}</small></article>
      <article><span>Water access</span><b>{context.spatial_context.water_access_index_relative_0_100.toFixed(1)} index</b><small>{context.spatial_context.nearest_permanent_surface_water_km.toFixed(2)} km to permanent surface water</small></article>
      <article><span>Grid snapshot</span><b>{forecast?.hotspot_prev_30d ?? 0} hotspots / 30d</b><small>{forecast?.rainfall_30d ?? "—"} mm rainfall / 30d</small></article>
      <article><span>Fire-weather FWI</span><b>{weather.lookup_status === "available" ? `${weather.fwi?.toFixed(1)} · ${weather.recommended_fwi_class.replaceAll("_", " ")}` : "Unavailable"}</b><small>{weather.weather_at_local_noon ? `${weather.weather_at_local_noon.temperature_c.toFixed(1)}°C · RH ${weather.weather_at_local_noon.relative_humidity_pct.toFixed(0)}% · ${weather.weather_at_local_noon.mode}` : weather.message}</small></article>
      <article><span>Field fire type</span><b>{fireType.recommended_fire_type_class === "unknown" ? "Not yet recorded" : fireType.recommended_fire_type_class.replaceAll("_", " ")}</b><small>{fireType.latest_observation ? `${fireType.latest_observation.confidence} · ${new Date(fireType.latest_observation.created_at_utc).toLocaleString("id-ID")}` : fireType.message}</small></article>
    </div>
  </section>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="field"><span>{label}</span>{children}</label>; }
function Select({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: string[][] }) { return <select value={value} onChange={(event) => onChange(event.target.value)}>{options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>; }
