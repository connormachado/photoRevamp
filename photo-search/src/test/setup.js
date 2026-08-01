// Vitest setup — runs before every test file.
import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Every component below <StatsProvider> triggers a real network call on mount:
// StatsContext's effect fetches http://localhost:5001/stats and retries 20 times
// at 1500ms intervals on failure. Un-stubbed, that leaves pending timers and
// unhandled rejections in every render test. So fetch is stubbed globally and
// each test overrides it via `globalThis.fetch.mockResolvedValueOnce(...)`.
// `globalThis` rather than `global` so the file passes the project's eslint
// config, which declares browser globals only.
beforeEach(() => {
  globalThis.fetch = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
})
