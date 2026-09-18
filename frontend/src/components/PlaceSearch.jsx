import { useState } from "react";
import { searchPlaces } from "../geocode";

// Jump the map to a named place — with the old state/district/village dropdowns gone, the only
// other way to find a village was panning across the whole country. Searches on submit only (no
// search-as-you-type), which is what Nominatim's usage policy asks of clients.
export default function PlaceSearch({ onPick }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null); // null = nothing searched yet
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    try {
      const found = await searchPlaces(q);
      // A single unambiguous hit needs no list — go straight there.
      if (found.length === 1) {
        onPick(found[0]);
        setResults(null);
      } else {
        setResults(found);
      }
    } catch (err) {
      setError(`Search failed: ${err.message}`);
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  function pick(place) {
    onPick(place);
    setResults(null);
    setQuery(place.name);
  }

  return (
    <div className="place-search">
      <form onSubmit={handleSubmit} className="place-search-form" role="search">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search a village, town or district"
          aria-label="Search for a place"
        />
        <button type="submit" className="btn btn-outline" disabled={busy || !query.trim()}>
          {busy ? "…" : "Go"}
        </button>
      </form>
      {error && <p className="place-search-note place-search-error">{error}</p>}
      {results && results.length === 0 && (
        <p className="place-search-note">No places in India match that name.</p>
      )}
      {results && results.length > 0 && (
        <ul className="place-search-results">
          {results.map((place) => (
            <li key={place.id}>
              <button type="button" onClick={() => pick(place)}>
                <span className="place-name">{place.name}</span>
                <span className="place-detail">{place.detail}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
