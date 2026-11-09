import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// El puerto 5173 es el que la API acepta por defecto en STUDYFLOW_CORS.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
