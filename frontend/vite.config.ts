import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1/session": { target: "ws://127.0.0.1:23333", ws: true, changeOrigin: true },
      "/v1": { target: "http://127.0.0.1:23333", changeOrigin: true },
    },
  },
});
