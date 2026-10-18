import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// The dev/preview servers proxy the backend so the browser stays same-origin (no CORS needed).
// ASTRA_BACKEND_URL (not exposed to the browser) defaults to the local FastAPI server.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.ASTRA_BACKEND_URL || 'http://127.0.0.1:8000';
  const proxy = { '/api': { target, changeOrigin: true }, '/health': { target, changeOrigin: true } };
  return { plugins: [react()], server: { proxy }, preview: { proxy } };
});
