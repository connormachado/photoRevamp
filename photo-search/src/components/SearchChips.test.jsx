/**
 * The junk-cull chips.
 *
 * `CHIPS` is the single source of truth: the chip row renders it, and Junk Hunt
 * re-imports it to fire every query in parallel. So its invariants are a real
 * contract between two features, not just data shape.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SearchChips, { CHIPS } from './SearchChips'

describe('CHIPS', () => {
  it('is a non-empty list', () => {
    expect(CHIPS.length).toBeGreaterThan(0)
  })

  it('gives every chip an emoji, a label and a query', () => {
    for (const chip of CHIPS) {
      expect(chip.emoji).toBeTruthy()
      expect(chip.label).toBeTruthy()
      expect(chip.query).toBeTruthy()
    }
  })

  it('keeps every query unique', () => {
    // `query` is the React key in the chip row AND the identity Junk Hunt keys
    // its parallel results by — a duplicate silently loses a result.
    const queries = CHIPS.map((c) => c.query)
    expect(new Set(queries).size).toBe(queries.length)
  })

  it('keeps emoji out of the query text', () => {
    // The whole reason emoji is its own field: it would just be noise in the
    // CLIP embedding.
    for (const chip of CHIPS) {
      expect(chip.query).not.toMatch(/\p{Extended_Pictographic}/u)
      expect(chip.query).not.toContain(chip.emoji)
    }
  })

  it('keeps queries as clean lowercase prompts with no stray whitespace', () => {
    for (const chip of CHIPS) {
      expect(chip.query).toBe(chip.query.trim())
      expect(chip.query).toBe(chip.query.toLowerCase())
      expect(chip.query).not.toMatch(/\s{2,}/)
    }
  })
})

describe('<SearchChips>', () => {
  it('renders one button per chip', () => {
    render(<SearchChips query="" onSearch={() => {}} />)
    expect(screen.getAllByRole('button')).toHaveLength(CHIPS.length)
  })

  it('shows each chip’s label', () => {
    render(<SearchChips query="" onSearch={() => {}} />)
    for (const chip of CHIPS) {
      expect(screen.getByText(chip.label)).toBeInTheDocument()
    }
  })

  it('fires a search with the clean query, not the label', () => {
    // The label carries human wording; only `query` should reach CLIP.
    const onSearch = vi.fn()
    render(<SearchChips query="" onSearch={onSearch} />)
    screen.getAllByRole('button')[0].click()
    expect(onSearch).toHaveBeenCalledWith(CHIPS[0].query)
  })

  it('highlights the chip matching the active query', () => {
    const active = CHIPS[2]
    render(<SearchChips query={active.query} onSearch={() => {}} />)
    const button = screen.getByText(active.label).closest('button')
    expect(button).toHaveStyle({ borderColor: '#818cf8' })
  })

  it('highlights nothing when the search box holds something else', () => {
    render(<SearchChips query="sunset over the ocean" onSearch={() => {}} />)
    for (const chip of CHIPS) {
      const button = screen.getByText(chip.label).closest('button')
      expect(button).not.toHaveStyle({ borderColor: '#818cf8' })
    }
  })
})
