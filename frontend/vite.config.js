import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend calls the API at a relative `/api`, so the same build works wherever it is served
// from. In dev, Vite forwards `/api` to the local backend — which also means no CORS in dev.
// Override the target with VITE_API_PROXY if the backend runs elsewhere.
const apiTarget = process.env.VITE_API_PROXY || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    // Libraries change far less often than app code; separate chunks keep them cached across
    // deploys and keep each chunk under Vite's size warning.
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          leaflet: ["leaflet", "@geoman-io/leaflet-geoman-free"],
        },
      },
    },
  },
  server: {
    port: 5500,
    proxy: { "/api": { target: apiTarget, changeOrigin: true } },
  },
  preview: {
    port: 5500,
    proxy: { "/api": { target: apiTarget, changeOrigin: true } },
  },
});
