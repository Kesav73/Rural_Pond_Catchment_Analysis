import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// Deliberately not wrapped in <StrictMode>. Its dev-only double-invoke of effects means the
// Leaflet map in MapView is built, torn down and rebuilt on every page load — which also throws
// away and re-issues the entire first batch of basemap tile requests, and initialises Geoman's
// toolbar twice. On a map-centric app that is the single biggest cost of a dev reload. The effect
// it was stress-testing is cleanup-safe (MapView's cleanup calls map.remove()); we just don't want
// to pay for that test on every reload.
createRoot(document.getElementById("root")).render(<App />);
