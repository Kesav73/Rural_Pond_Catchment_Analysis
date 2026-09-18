import { catchmentHa, expectedVolume } from "./format";

// Everything on the map for this run as one GeoJSON file: a pond polygon per site and, where it
// was traced, its catchment polygon. Built entirely client-side from what the API already sent.
export function downloadResultsGeoJSON(features, catchmentsByRank, summary) {
  const out = [];
  for (const feature of features) {
    const p = feature.properties;
    out.push({
      type: "Feature",
      geometry: feature.geometry,
      properties: {
        kind: "pond_site",
        rank: p.rank,
        expected_volume_m3: Math.round(expectedVolume(p) ?? 0),
        volume_basis: "one design storm",
        design_storm_mm: summary?.design_storm_mm ?? null,
        pond_area_ha: p.area_ha,
        capacity_m3: p.capacity_m3,
        runoff_m3: p.runoff_m3,
        catchment_area_ha: catchmentHa(p, catchmentsByRank[p.rank]),
        fill_ratio: p.fill_ratio,
        mean_depth_m: p.mean_depth_m,
        max_depth_m: p.max_depth_m,
        centroid_lat: p.centroid?.lat,
        centroid_lon: p.centroid?.lon,
      },
    });
    const catchment = catchmentsByRank[p.rank];
    if (catchment && catchment.geometry) {
      out.push({
        type: "Feature",
        geometry: catchment.geometry,
        properties: {
          kind: "catchment",
          rank: p.rank,
          area_ha: catchment.area_ha,
          pour_point_lat: catchment.pour_point?.lat,
          pour_point_lon: catchment.pour_point?.lon,
          warnings: (catchment.warnings || []).join(" | "),
        },
      });
    }
  }

  const blob = new Blob([JSON.stringify({ type: "FeatureCollection", features: out }, null, 2)], {
    type: "application/geo+json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `pond-sites-${new Date().toISOString().slice(0, 10)}.geojson`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
