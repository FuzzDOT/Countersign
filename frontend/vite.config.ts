import { defineConfig, type UserConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },

  server: {
    host: '0.0.0.0',
    port: 5173,
    // Required for HMR to work through a Docker bind mount on some hosts.
    watch: { usePolling: true },
    proxy: {
      // Hitting /api from the dev server avoids CORS entirely in development.
      '/api': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://backend:8000',
        changeOrigin: true,
        ws: true, // job progress websocket
      },
    },
  },

  build: {
    target: 'es2022',
    sourcemap: true,
    // Fail the build if a chunk gets fat. The landing route budget is 180KB
    // gzipped — raising this number instead of splitting is the wrong fix.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          // These are only needed on /app/graph and /app/evidence.
          // Keeping them out of the landing bundle is the whole point.
          graph: ['d3-force', 'd3-zoom', 'd3-selection', 'd3-scale', 'd3-shape'],
          charts: ['recharts'],
          motion: ['framer-motion'],
        },
      },
    },
  },

  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
    css: false,
    coverage: {
      provider: 'v8',
      exclude: ['src/api/schema.d.ts', 'src/test-setup.ts', '**/*.d.ts'],
    },
  },
} as UserConfig);
