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

  it('gives every chip an id, an emoji, a label and a query', () => {
    for (const chip of CHIPS) {
      expect(chip.id).toBeTruthy()
      expect(chip.emoji).toBeTruthy()
      expect(chip.label).toBeTruthy()
      expect(chip.query).toBeTruthy()
    }
  })

  it('keeps every query unique', () => {
    // `query` is what's sent to CLIP for both a chip search and Junk Hunt —
    // a duplicate is confusing but not identity-breaking, unlike `id`.
    const queries = CHIPS.map((c) => c.query)
    expect(new Set(queries).size).toBe(queries.length)
  })

  it('keeps every id unique', () => {
    // `id` is the React key AND the persisted dismissal-ledger key — a
    // duplicate would silently merge two chips' hide lists.
    const ids = CHIPS.map((c) => c.id)
    expect(new Set(ids).size).toBe(ids.length)
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

  it('fires a search with the whole chip object, keyed for the dismissal ledger', () => {
    // The caller needs both `query` (what reaches CLIP) and `id` (the
    // ledger key), so onSearch receives the chip, not a bare string.
    const onSearch = vi.fn()
    render(<SearchChips query="" onSearch={onSearch} />)
    screen.getAllByRole('button')[0].click()
    expect(onSearch).toHaveBeenCalledWith(CHIPS[0])
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
