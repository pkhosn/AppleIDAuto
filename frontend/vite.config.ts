import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  base: mode === 'cutover' ? '/control-app/' : '/react-preview/',
  build: {
    outDir: mode === 'cutover' ? '../public/control-app' : '../public/react-preview',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('react-router') || id.includes('/react/') || id.includes('/react-dom/')) return 'react'
          if (id.includes('@tanstack')) return 'query'
          if (id.includes('react-hook-form') || id.includes('@hookform') || id.includes('/zod/')) return 'forms'
          if (id.includes('@base-ui') || id.includes('lucide-react') || id.includes('/sonner/') || id.includes('next-themes')) return 'ui'
          return 'vendor'
        },
      },
    },
  },
}))
