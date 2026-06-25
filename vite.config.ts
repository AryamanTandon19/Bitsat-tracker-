import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
// `base` is "/" for local dev/preview and is overridden to the repo sub-path
// (e.g. "/Bitsat-tracker-/") when building for GitHub Pages via VITE_BASE.
export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
  },
});
