import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend (uv run assetgen) listens on 127.0.0.1:8000; /api and its WebSocket go there.
// The Host header is kept, so the file URLs the backend returns point back at this dev server.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", ws: true },
    },
  },
  // The 3D viewer chunk is mostly three.js and is loaded only on the gates that show a model.
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 800 },
});
