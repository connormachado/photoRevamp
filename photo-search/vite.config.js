import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    include: ['src/**/*.test.{js,jsx}'],
    // Deliberately NOT `globals: true`. eslint.config.js only declares browser
    // globals, so bare `describe`/`it`/`expect` would fail `npm run lint` with
    // no-undef. Importing them from 'vitest' in each file keeps one lint config
    // for the whole project.
    globals: false,
  },
})
