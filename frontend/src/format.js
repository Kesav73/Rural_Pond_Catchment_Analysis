// One place for how numbers read, so the map labels, cards and popups never disagree on a figure.

// International grouping (137,037). Lakh-style grouping (1,37,037) read as a different number to
// users, so lakhs are given separately, in words, where a figure is large enough to need them.
const intFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function m3(value) {
  return `${intFmt.format(Math.round(value))} m³`;
}

// 137,037 → "1.37 lakh m³". Callers use it only for figures of a lakh or more; below that the
// plain figure already reads fine.
export function lakh(value) {
  return `${(value / 100_000).toFixed(2)} lakh m³`;
}

export function km2(hectares) {
  return `${(hectares / 100).toFixed(hectares >= 100 ? 1 : 2)} km²`;
}

export function ha(value, digits = 1) {
  return `${value.toFixed(digits)} ha`;
}

export function pct(fraction) {
  return `${Math.round(fraction * 100)}%`;
}

// A site's headline water figure — `expected_volume_m3` from the API (min of capacity and one
// storm's runoff). Older cached responses predate the field, so fall back to the same formula.
export function expectedVolume(properties) {
  if (properties.expected_volume_m3 != null) return properties.expected_volume_m3;
  if (properties.capacity_m3 != null && properties.runoff_m3 != null) {
    return Math.min(properties.capacity_m3, properties.runoff_m3);
  }
  return null;
}

// Catchment area in ha. The traced outline (/api/catchment) is the authoritative figure — it is
// what the drainage warnings are computed from — so it wins once it exists; until then, the
// ranking step's estimate from flow accumulation.
export function catchmentHa(properties, catchment) {
  if (catchment && catchment.area_ha != null) return catchment.area_ha;
  if (properties.catchment_area_m2 != null) return properties.catchment_area_m2 / 10000;
  return null;
}

// Whether one design storm fills the pond: the question a planner actually asks of a site.
export function fillStatus(properties) {
  const fill = properties.fill_ratio;
  if (fill == null) return null;
  if (fill >= 1) return { tone: "good", label: "Fills in one storm" };
  if (fill >= 0.5) return { tone: "warn", label: `Fills to ${pct(fill)}` };
  return { tone: "bad", label: `Only ${pct(fill)} full` };
}
