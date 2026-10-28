import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Build the real application with DEV=false and a synthetic native adapter.
export default defineConfig({
  plugins: [vue()],
  build: { outDir: 'evidence/desktop-qa', rollupOptions: { input: 'tests/desktop-qa.html' } },
})
