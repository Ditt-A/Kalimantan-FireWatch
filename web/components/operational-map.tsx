"use client";

import { useEffect, useRef } from "react";
import type { Alert, FireStationLandmark, PriorityCell, ResponseCandidate } from "@/lib/api";
import "maplibre-gl/dist/maplibre-gl.css";

type Props = {
  cells: PriorityCell[];
  alerts: Alert[];
  scope: "priority" | "peat" | "mineral";
  fireStationLandmarks: FireStationLandmark[];
  responseCandidates: ResponseCandidate[];
  selectedAlertId: number | null;
  dimAlerts: boolean;
  selectedCandidateId: string | null;
  routeCoordinates: number[][];
  onAlertClick: (alert: Alert) => void;
  onCellClick: (cell: PriorityCell) => void;
  onCandidateClick: (candidate: ResponseCandidate) => void;
};

const priorityColor: Record<string, string> = {
  Critical: "#f35c38", High: "#ff9d3f", Watch: "#f4c85c", Low: "#607d8b",
};

export function OperationalMap({ cells, alerts, scope, fireStationLandmarks, responseCandidates, selectedAlertId, dimAlerts, selectedCandidateId, routeCoordinates, onAlertClick, onCellClick, onCandidateClick }: Props) {
  const container = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<any>(null);
  const alertById = useRef(new Map<number, Alert>());
  const cellById = useRef(new Map<string, PriorityCell>());
  const candidateById = useRef(new Map<string, ResponseCandidate>());
  const cellsRef = useRef(cells);
  const alertsRef = useRef(alerts);
  const scopeRef = useRef(scope);
  const candidatesRef = useRef(responseCandidates);
  const selectedCandidateRef = useRef(selectedCandidateId);
  const selectedAlertRef = useRef(selectedAlertId);
  const dimAlertsRef = useRef(dimAlerts);
  const routeRef = useRef(routeCoordinates);
  const landmarksRef = useRef(fireStationLandmarks);
  const candidateClickRef = useRef(onCandidateClick);
  cellsRef.current = cells;
  alertsRef.current = alerts;
  scopeRef.current = scope;
  candidatesRef.current = responseCandidates;
  selectedCandidateRef.current = selectedCandidateId;
  selectedAlertRef.current = selectedAlertId;
  dimAlertsRef.current = dimAlerts;
  routeRef.current = routeCoordinates;
  landmarksRef.current = fireStationLandmarks;
  candidateClickRef.current = onCandidateClick;

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    let disposed = false;
    void import("maplibre-gl").then(({ default: maplibregl }) => {
      if (disposed || !container.current) return;
      // Self-hosted OSM has priority in production. The public fallback only
      // prevents a blank map during local development before it is configured.
      const tileUrl = process.env.NEXT_PUBLIC_OSM_TILE_URL ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
      const style: any = {
        version: 8,
        sources: { osm: { type: "raster", tiles: [tileUrl], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      };
      const map = new maplibregl.Map({
        container: container.current,
        style,
        center: [114.0, -0.4],
        zoom: 5.0,
        minZoom: 4.4,
        maxBounds: [[107.8, -4.7], [119.7, 4.6]],
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
      map.on("load", () => {
        map.addSource("priority", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addLayer({ id: "priority-circles", type: "circle", source: "priority", paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "probability"], 0, 3, 1, 13],
          "circle-color": ["get", "tone"],
          "circle-opacity": 0.72, "circle-stroke-color": "#10251f", "circle-stroke-width": 0.45,
        } });
        // Grouping is performed by the API with the operator-selected
        // kilometre radius. Do not apply MapLibre's pixel-radius clustering on
        // top of it, because that would hide changes made in the controls.
        map.addSource("alerts", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addLayer({ id: "alert-points", type: "circle", source: "alerts", paint: {
          "circle-radius": ["+", ["interpolate", ["linear"], ["get", "frp"], 0, 4, 50, 12], ["interpolate", ["linear"], ["get", "detection_count"], 1, 0, 5, 1.5, 25, 3.5, 100, 6]],
          "circle-color": "#9f1d22", "circle-opacity": 0.9, "circle-stroke-color": "#fff9ed", "circle-stroke-opacity": 0.9, "circle-stroke-width": 1.5,
        }, filter: ["!", ["has", "point_count"]] });
        map.addLayer({ id: "alert-clusters", type: "circle", source: "alerts", filter: ["has", "point_count"], paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "point_count"], 2, 13, 25, 19, 100, 26, 500, 34, 3000, 42],
          "circle-color": "#9f1d22", "circle-opacity": 0.92, "circle-stroke-color": "#fff8dc", "circle-stroke-width": 2.5,
        } });
        map.addLayer({ id: "selected-alert-halo", type: "circle", source: "alerts", filter: ["==", ["get", "id"], -1], paint: {
          "circle-radius": ["+", ["interpolate", ["linear"], ["get", "frp"], 0, 4, 50, 12], 7],
          "circle-color": "#ffd166", "circle-opacity": 0.48,
        } });
        map.addLayer({ id: "selected-alert-point", type: "circle", source: "alerts", filter: ["==", ["get", "id"], -1], paint: {
          "circle-radius": ["+", ["interpolate", ["linear"], ["get", "frp"], 0, 4, 50, 12], 1],
          "circle-color": "#9f1d22", "circle-opacity": 1, "circle-stroke-color": "#fff9ed", "circle-stroke-width": 2.4,
        } });
        map.addSource("fire-station-landmarks", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addLayer({ id: "fire-station-halo", type: "circle", source: "fire-station-landmarks", paint: {
          "circle-radius": 8, "circle-color": "#fff8e6", "circle-opacity": 0.92,
        } });
        map.addLayer({ id: "fire-station-points", type: "circle", source: "fire-station-landmarks", paint: {
          "circle-radius": 4.6, "circle-color": "#1c5b47", "circle-opacity": 1, "circle-stroke-color": "#123d30", "circle-stroke-width": 1.2,
        } });
        map.addSource("response-route", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addLayer({ id: "response-route-halo", type: "line", source: "response-route", paint: {
          "line-color": "#fff8dc", "line-width": 13, "line-opacity": 0.94, "line-blur": 0.7,
        } });
        map.addLayer({ id: "response-route-line", type: "line", source: "response-route", paint: {
          "line-color": "#d9482c", "line-width": 6, "line-opacity": 1,
        } });
        map.addSource("response-locations", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addLayer({ id: "response-location-points", type: "circle", source: "response-locations", paint: {
          "circle-radius": ["case", ["==", ["get", "selected"], true], 10, 6],
          "circle-color": ["match", ["get", "mode"], "ground", "#1c5b47", "#2563eb"],
          "circle-opacity": 0.95, "circle-stroke-color": "#fff9ed", "circle-stroke-width": ["case", ["==", ["get", "selected"], true], 2.5, 1.2],
        } });
        map.on("mouseenter", "alert-points", () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "alert-points", () => { map.getCanvas().style.cursor = ""; });
        map.on("click", "alert-points", (event: any) => {
          const id = Number(event.features?.[0]?.properties?.id);
          const alert = alertById.current.get(id);
          if (alert) onAlertClick(alert);
        });
        map.on("mouseenter", "alert-clusters", () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "alert-clusters", () => { map.getCanvas().style.cursor = ""; });
        map.on("click", "alert-clusters", (event: any) => {
          const feature = event.features?.[0];
          if (!feature) return;
          const properties = feature.properties ?? {};
          const [longitude, latitude] = feature.geometry.coordinates;
          const northSouthKm = Math.abs(Number(properties.latitude_max) - Number(properties.latitude_min)) * 111.32;
          const meanLatitude = (Number(properties.latitude_max) + Number(properties.latitude_min)) / 2;
          const eastWestKm = Math.abs(Number(properties.longitude_max) - Number(properties.longitude_min)) * 111.32 * Math.cos(meanLatitude * Math.PI / 180);
          const summary = `${Number(properties.detection_total).toLocaleString("id-ID")} detections · FRP ${Number(properties.frp_min).toFixed(1)}–${Number(properties.frp_max).toFixed(1)} MW · point extent ≈ ${northSouthKm.toFixed(1)} × ${eastWestKm.toFixed(1)} km`;
          new maplibregl.Popup({ offset: 12, closeButton: false }).setLngLat([longitude, latitude]).setText(`${summary}. Click again or zoom in to inspect individual detections.`).addTo(map);
          const source: any = map.getSource("alerts");
          source?.getClusterExpansionZoom(properties.cluster_id, (error: Error | null, zoom: number) => {
            if (!error) map.easeTo({ center: [longitude, latitude], zoom, duration: 500 });
          });
        });
        map.on("mouseenter", "priority-circles", () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "priority-circles", () => { map.getCanvas().style.cursor = ""; });
        map.on("click", "priority-circles", (event: any) => {
          const gridId = event.features?.[0]?.properties?.grid;
          if (typeof gridId === "string") onCellClick(cellById.current.get(gridId) as PriorityCell);
        });
        map.on("mouseenter", "fire-station-points", () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "fire-station-points", () => { map.getCanvas().style.cursor = ""; });
        map.on("click", "fire-station-points", (event: any) => {
          const feature = event.features?.[0];
          if (!feature) return;
          const [longitude, latitude] = feature.geometry.coordinates;
          const place = [feature.properties?.district, feature.properties?.province].filter(Boolean).join(", ");
          new maplibregl.Popup({ offset: 10, closeButton: false }).setLngLat([longitude, latitude]).setText(`${feature.properties?.name ?? "Fire station"}${place ? ` · ${place}` : ""}`).addTo(map);
        });
        map.on("mouseenter", "response-location-points", () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "response-location-points", () => { map.getCanvas().style.cursor = ""; });
        map.on("click", "response-location-points", (event: any) => {
          const candidateId = event.features?.[0]?.properties?.candidate;
          if (typeof candidateId === "string") candidateClickRef.current(candidateById.current.get(candidateId) as ResponseCandidate);
        });
        setPriorityData(map, cellsRef.current, scopeRef.current);
        setAlertData(map, alertsRef.current, selectedAlertRef.current);
        setAlertEmphasis(map, selectedAlertRef.current, dimAlertsRef.current);
        setFireStationLandmarkData(map, landmarksRef.current);
        setResponseLocationData(map, candidatesRef.current, selectedCandidateRef.current);
        setRouteData(map, routeRef.current);
      });
      mapRef.current = map;
    });
    return () => { disposed = true; mapRef.current?.remove(); mapRef.current = null; };
  }, [onAlertClick, onCellClick]);

  useEffect(() => {
    cellById.current = new Map(cells.map((cell) => [cell.grid_id, cell]));
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    setPriorityData(map, cells, scope);
  }, [cells, scope]);

  useEffect(() => {
    alertById.current = new Map(alerts.map((alert) => [alert.detection_id, alert]));
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    setAlertData(map, alerts, selectedAlertId);
    setAlertEmphasis(map, selectedAlertId, dimAlerts);
  }, [alerts, selectedAlertId, dimAlerts]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    setFireStationLandmarkData(map, fireStationLandmarks);
  }, [fireStationLandmarks]);

  useEffect(() => {
    candidateById.current = new Map(responseCandidates.map((candidate) => [candidate.candidate_id, candidate]));
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    setResponseLocationData(map, responseCandidates, selectedCandidateId);
  }, [responseCandidates, selectedCandidateId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    setRouteData(map, routeCoordinates);
    if (routeCoordinates.length >= 2) {
      let minLongitude = routeCoordinates[0][0]; let maxLongitude = routeCoordinates[0][0];
      let minLatitude = routeCoordinates[0][1]; let maxLatitude = routeCoordinates[0][1];
      for (const coordinate of routeCoordinates.slice(1)) {
        minLongitude = Math.min(minLongitude, coordinate[0]); maxLongitude = Math.max(maxLongitude, coordinate[0]);
        minLatitude = Math.min(minLatitude, coordinate[1]); maxLatitude = Math.max(maxLatitude, coordinate[1]);
      }
      const bounds: [[number, number], [number, number]] = [[minLongitude, minLatitude], [maxLongitude, maxLatitude]];
      map.fitBounds(bounds, { padding: 72, maxZoom: 10, duration: 550 });
    }
  }, [routeCoordinates]);

  return <div ref={container} className="operational-map" aria-label="Map of forecast priority grids and FIRMS satellite alerts" />;
}

function setPriorityData(map: any, cells: PriorityCell[], scope: "priority" | "peat" | "mineral") {
  const source = map.getSource("priority");
  if (!source) return;
  source.setData({ type: "FeatureCollection", features: cells.map((cell) => ({
    type: "Feature", properties: { priority: cell.priority_category, probability: cell.predicted_fire_probability, grid: cell.grid_id, tone: scope === "mineral" ? "#3b82f6" : priorityColor[cell.priority_category] ?? "#607d8b" },
    geometry: { type: "Point", coordinates: [cell.centroid_lon, cell.centroid_lat] },
  })) });
}

function setAlertData(map: any, alerts: Alert[], selectedAlertId: number | null) {
  const source = map.getSource("alerts");
  if (!source) return;
  source.setData({ type: "FeatureCollection", features: alerts.map((alert) => ({
    type: "Feature", properties: { id: alert.detection_id, frp: alert.frp, latitude: alert.latitude, longitude: alert.longitude, detection_count: alert.detection_count ?? 1, selected: alert.detection_id === selectedAlertId, hasSelectedAlert: selectedAlertId !== null },
    geometry: { type: "Point", coordinates: [alert.longitude, alert.latitude] },
  })) });
}

function setAlertEmphasis(map: any, selectedAlertId: number | null, dimAlerts: boolean) {
  const dimmed = selectedAlertId !== null || dimAlerts;
  map.setPaintProperty("alert-points", "circle-opacity", dimmed ? 0.3 : 0.9);
  map.setPaintProperty("alert-points", "circle-stroke-opacity", dimmed ? 0.3 : 0.9);
  map.setPaintProperty("alert-clusters", "circle-opacity", dimmed ? 0.3 : 0.92);
  map.setFilter("selected-alert-halo", ["==", ["get", "id"], selectedAlertId ?? -1]);
  map.setFilter("selected-alert-point", ["==", ["get", "id"], selectedAlertId ?? -1]);
}

function setFireStationLandmarkData(map: any, landmarks: FireStationLandmark[]) {
  const source = map.getSource("fire-station-landmarks");
  if (!source) return;
  source.setData({ type: "FeatureCollection", features: landmarks.map((landmark) => ({
    type: "Feature", properties: { name: landmark.name, province: landmark.province, district: landmark.district },
    geometry: { type: "Point", coordinates: [landmark.longitude, landmark.latitude] },
  })) });
}

function setResponseLocationData(map: any, candidates: ResponseCandidate[], selectedCandidateId: string | null) {
  const source = map.getSource("response-locations");
  if (!source) return;
  source.setData({ type: "FeatureCollection", features: candidates.map((candidate) => ({
    type: "Feature", properties: { candidate: candidate.candidate_id, mode: candidate.response_mode, selected: candidate.candidate_id === selectedCandidateId },
    geometry: { type: "Point", coordinates: [candidate.longitude, candidate.latitude] },
  })) });
}

function setRouteData(map: any, coordinates: number[][]) {
  const source = map.getSource("response-route");
  if (!source) return;
  source.setData({ type: "FeatureCollection", features: coordinates.length >= 2 ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates } }] : [] });
}

export function PriorityLegend({ scope, showResponseLocations = false }: { scope: "priority" | "peat" | "mineral"; showResponseLocations?: boolean }) {
  const fireStationLegend = <span><i style={{ background: "#1c5b47" }} />Fire station</span>;
  const responseLegend = showResponseLocations ? <span><i style={{ background: "#2563eb" }} />Aviation access</span> : null;
  if (scope === "mineral") return <div className="map-legend"><span><i style={{ background: "#3b82f6" }} />SoilGrids mineral reference</span>{fireStationLegend}{responseLegend}</div>;
  return <div className="map-legend">{Object.entries(priorityColor).map(([label, color]) => <span key={label}><i style={{ background: color }} />{label}</span>)}{scope === "priority" && <span><b>●</b> FIRMS alert</span>}{fireStationLegend}{responseLegend}</div>;
}
