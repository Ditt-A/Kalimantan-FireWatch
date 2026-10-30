// Keep browser requests on the current origin by default. Next.js proxies this
// path to the local FastAPI service, which also makes a single public tunnel
// work without sending visitors to their own localhost:8000.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: init?.cache ?? "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `API request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export type MetadataStatus = {
  status: string;
  not_live: boolean;
  message: string;
  prediction_snapshot: { week_start_min: string; week_start_max: string; rows: number };
  firms_snapshot: { date_min: string; date_max: string; detections: number; source: string };
  evidence: { local_response_cases: number; vehicle_evidence_records: number };
};

export type PriorityCell = {
  grid_id: string; week_start: string; province: string; district: string;
  centroid_lat: number; centroid_lon: number; peat_pct: number;
  histosols_probability_pct: number | null; mineral_reference: boolean | null; rainfall_30d: number | null;
  hotspot_prev_30d: number; predicted_fire_probability: number;
  predicted_hotspot_count_next_7d: number; risk_category: string; count_category: string; priority_category: string;
};

export type PriorityResponse = {
  snapshot_week: string; total_matches: number; returned: number;
  priority_summary: { Critical: number; High: number; Watch: number; Low: number };
  map_scope: "all_priority_grids" | "big_peat_context_grids" | "soilgrids_mineral_reference_grids";
  items: PriorityCell[];
};

export type Alert = {
  detection_id: number; latitude: number; longitude: number; acq_date: string; acq_datetime: string;
  confidence: string; frp: number; satellite: string; instrument: string; daynight: string;
  source_product: string; grid_id: string; province: string; district: string;
  display_group_id?: string; detection_count?: number; display_group_status?: string;
  latitude_min?: number; latitude_max?: number; longitude_min?: number; longitude_max?: number;
  frp_min?: number; frp_max?: number; frp_total?: number;
  footprint_ha_proxy_total?: number; footprint_ha_proxy_min?: number; footprint_ha_proxy_max?: number;
};

export type AlertResponse = { snapshot_date: string; total_matches: number; items: Alert[] };

export type AlertDisplayGroupsResponse = {
  snapshot_date: string; total_detections: number; display_group_count: number;
  minimum_footprint_ha: number; merge_radius_km: number; footprint_note: string; items: Alert[];
};

export type ResponsePlan = {
  status: string; automatic_dispatch_permitted: boolean;
  spatial_context: { grid_id: string; province: string; district: string; grid_match_status: string; nearest_permanent_surface_water_km: number; nearest_any_surface_water_km: number; water_access_index_relative_0_100: number; water_access_class: string };
  planning_output: { planned_personnel: number; ground_response_fire_engine_capacity: number; fire_engine_formula: string; planning_confidence: string; complexity_level: string };
  benchmark_context: { source: string; area_band: string; peak_personnel_median: number; peak_personnel_p90: number };
  applied_conditions: string[]; requirements: string[]; limitations: string[];
  evidence_cases: Array<{ incident_id: string; province: string; district: string; area_ha: number; soil_type: string; fire_type: string | null; personnel_total: number | null; documented_ground_response_vehicle_total: number | null; resource_mapping_confidence: string; source_document_id: string; source_document_url_retrieved: string; source_page: string }>;
};

export type PlannerContext = {
  coordinate: { latitude: number; longitude: number };
  data_status: string;
  spatial_context: {
    grid_id: string; province: string; district: string; grid_match_status: string;
    nearest_permanent_surface_water_km: number; nearest_any_surface_water_km: number;
    water_access_index_relative_0_100: number;
    forecast_grid: PriorityCell | null;
  };
  peat_context: {
    lookup_status: string; mapped_as_peat: boolean | null; recommended_soil_class: "peat" | "unknown";
    message: string; source?: string; peat_thick_class?: string | null; thickness_gt_1_5m_status?: string | null;
    planning_soil_basis?: string; grid_peat_coverage_pct?: number | null;
  };
  soil_context: {
    lookup_status: string; recommended_soil_class: "peat" | "mineral" | "unknown";
    histosols_probability_pct?: number; mineral_reference?: boolean; agreement_status?: string;
    message: string; source?: string; source_url?: string; source_retrieved_at_utc?: string; resolution_note?: string;
  };
  fire_weather_context: {
    lookup_status: string; recommended_fwi_class: "low" | "moderate" | "high" | "very_high" | "unknown";
    fwi?: number; ffmc?: number; dmc?: number; dc?: number; isi?: number; bui?: number;
    weather_at_local_noon?: { timestamp: string; temperature_c: number; relative_humidity_pct: number; wind_kmh: number; rainfall_previous_24h_mm: number; mode: string };
    source?: { provider: string; fetched_at_utc: string }; message?: string;
  };
  fire_type_context: {
    lookup_status: string; recommended_fire_type_class: "surface" | "ground_smouldering" | "mixed" | "crown" | "unknown";
    latest_observation?: FieldObservation | null; message: string;
  };
  model_input_defaults: {
    latitude: number; longitude: number; grid_id: string; area_ha: number | null;
    soil_class: "peat" | "mineral" | "unknown";
    fire_type_class: "surface" | "ground_smouldering" | "mixed" | "crown" | "unknown";
    fwi_class: "low" | "moderate" | "high" | "very_high" | "unknown";
    field_water_note: null;
    sources: { soil_class: string; fire_type_class: string; fwi_class: string; area_ha: string };
  };
};

export type FieldObservation = {
  observation_id: string; created_at_utc: string; grid_id: string | null; latitude: number; longitude: number;
  fire_type_class: "surface" | "ground_smouldering" | "mixed" | "crown"; confidence: "reported" | "confirmed";
  note: string | null; source: string;
};

export type ResponseCandidate = {
  candidate_id: string; response_mode: "ground" | "air"; osm_type: string; osm_id: number;
  name: string; latitude: number; longitude: number; province: string | null; district: string | null;
  straight_line_distance_km: number; facility_type: string | null; source: string | null; license: string | null;
  source_url: string | null; availability_status: string;
};

export type ResponseCandidates = {
  incident: { latitude: number; longitude: number }; ground_radius_km: number; air_radius_km: number;
  ground_candidates: ResponseCandidate[]; air_candidates: ResponseCandidate[];
  sources: { ground: string; air: string }; limitations: string[];
};

export type FireStationLandmark = {
  osm_type: string; osm_id: number; name: string; latitude: number; longitude: number;
  province: string | null; district: string | null; source: string | null; license: string | null; osm_url: string | null;
};

export type FireStationLandmarks = {
  total_matches: number; items: FireStationLandmark[]; source: string; limitation: string;
};

export type ResponseRoute = {
  candidate_id: string; response_mode: "ground" | "air"; transport_mode: "ground" | "helicopter";
  candidate: { name: string; latitude: number; longitude: number; source: string | null; source_url: string | null };
  incident: { latitude: number; longitude: number };
  route: { route_status: string; distance_km: number; duration_min: number | null; coordinates: number[][]; source: string; limitation: string };
};
