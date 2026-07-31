/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Dev-only: proxy API calls to the existing FastAPI backend (unchanged, per the design
    // doc -- this rewrite is frontend-only). Run `uvicorn backend.app:app --port 8123`
    // alongside `npm run dev`.
    proxy: {
      '/api': 'http://127.0.0.1:8123',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
  },
})
