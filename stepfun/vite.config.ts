import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8787",
    },
  },
  worker: { format: "es" },
  optimizeDeps: {
    exclude: ["@huggingface/transformers"],
  },
  build: {
    target: "esnext",
  },
});
