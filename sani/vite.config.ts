import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
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
