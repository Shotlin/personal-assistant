import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  // The shareable preview build escapes non-ASCII so every file is plain text.
  esbuild: process.env.VITE_SANI_PREVIEW === "1" ? { charset: "ascii" } : undefined,
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    target: "es2021",
    rollupOptions: {
      input: {
        main: resolve(__dirname, "app.html"),
        pill: resolve(__dirname, "index.html"),
        panel: resolve(__dirname, "panel.html"),
        onboarding: resolve(__dirname, "onboarding.html"),
      },
    },
  },
});
