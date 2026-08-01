/**
 * Harness smoke test
 * ==================
 * Proves the runner, the jsdom environment, the JSX transform, jest-dom
 * matchers, and the global fetch stub all work. If this file fails, no other
 * frontend test result means anything.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useState } from 'react'

function Counter() {
  const [n, setN] = useState(0)
  return (
    <div>
      <span data-testid="count">{n}</span>
      <button onClick={() => setN((v) => v + 1)}>bump</button>
    </div>
  )
}

describe('vitest harness', () => {
  it('runs a plain assertion', () => {
    expect(1 + 1).toBe(2)
  })

  it('provides a jsdom document', () => {
    expect(typeof document).toBe('object')
    expect(document.body).toBeTruthy()
  })

  it('renders JSX and exposes jest-dom matchers', () => {
    render(<Counter />)
    expect(screen.getByTestId('count')).toBeInTheDocument()
    expect(screen.getByTestId('count')).toHaveTextContent('0')
    expect(screen.getByRole('button', { name: 'bump' })).toBeEnabled()
  })

  it('stubs fetch globally so mounting never hits localhost:5001', async () => {
    // StatsContext fetches on mount and retries 20x; an unstubbed fetch would
    // leave pending timers in every render test.
    expect(globalThis.fetch).toBeDefined()
    const res = await fetch('http://localhost:5001/stats')
    expect(res.ok).toBe(true)
    expect(await res.json()).toEqual({})
  })
})
