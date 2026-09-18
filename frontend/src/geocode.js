// Place-name search via OpenStreetMap's Nominatim. Limited to India, which is the only region the
// elevation, water-cover and rainfall pipeline has been verified for.
// Usage policy: https://operations.osmfoundation.org/policies/nominatim/ — max 1 request/second,
// no autocomplete, attribution shown (the map already credits OpenStreetMap contributors).
const NOMINATIM = "https://nominatim.openstreetmap.org/search";

export async function searchPlaces(query) {
  const params = new URLSearchParams({
    q: query,
    format: "jsonv2",
    countrycodes: "in",
    limit: "6",
    addressdetails: "1",
  });
  const res = await fetch(`${NOMINATIM}?${params}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const rows = await res.json();
  return rows.map((row) => {
    const [south, north, west, east] = row.boundingbox.map(Number);
    const address = row.address || {};
    const name = row.name || row.display_name.split(",")[0];
    // "Abhanpur, Raipur, Chhattisgarh" reads better than Nominatim's full display_name.
    const detail = [address.county || address.state_district, address.state]
      .filter((part) => part && part !== name)
      .join(", ");
    return {
      id: row.place_id,
      name,
      detail: detail || row.type,
      bounds: [
        [south, west],
        [north, east],
      ],
    };
  });
}
