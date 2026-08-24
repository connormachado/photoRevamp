/**
 * The junk-cull chip row.
 *
 * The chip LIST no longer lives here — it comes from the backend chip store
 * (`photo_db/chips.json`, served by GET /chips), so the data invariants that
 * used to be tested in this file (unique ids, unique prompts, no emoji in the
 * prompt, lowercase/trimmed) moved to the backend suite `tests/test_chips.py`,
 * where the schema is actually enforced. What's left here is the component's
 * own contract: render what it's given, highlight the active one, and hand the
 * whole chip object back on click.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SearchChips from './SearchChips'

// The wire shape GET /chips returns. Deliberately literal rather than imported
// from anywhere — this file is the frontend's record of what it expects to be
// handed, so a backend shape change should turn it red rather than follow along.
const CHIPS = [
  {
    id: 'accidental',
    label: 'Accidental photo',
    emoji: '📷',
    builtin: true,
    enabled: true,
    order: 0,
    engine: 'semantic',
    query: { prompts: ['accidental photo'], negatives: [] },
    result_size: 24,
  },
  {
    id: 'dark',
    label: 'Dark or underexposed',
    emoji: '🌑',
    builtin: true,
    enabled: true,
    order: 1,
    engine: 'semantic',
    query: { prompts: ['dark or underexposed photo'], negatives: [] },
    result_size: 24,
  },
  {
    id: 'blurry',
    label: 'Blurry or out of focus',
    emoji: '💨',
    builtin: true,
    enabled: true,
    order: 2,
    engine: 'semantic',
    query: { prompts: ['blurry or out of focus photo'], negatives: [] },
    result_size: 24,
  },
]

describe('<SearchChips>', () => {
  it('renders one button per chip it is given', () => {
    render(<SearchChips chips={CHIPS} query="" onSearch={() => {}} />)
    expect(screen.getAllByRole('button')).toHaveLength(CHIPS.length)
  })

  it('shows each chip’s label', () => {
    render(<SearchChips chips={CHIPS} query="" onSearch={() => {}} />)
    for (const chip of CHIPS) {
      expect(screen.getByText(chip.label)).toBeInTheDocument()
    }
  })

  it('renders nothing before the chip list has been fetched', () => {
    // The list arrives asynchronously from GET /chips, so the first paint has
    // an empty array. It must render an empty row, not crash.
    render(<SearchChips chips={[]} query="" onSearch={() => {}} />)
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('survives the prop being omitted entirely', () => {
    render(<SearchChips query="" onSearch={() => {}} />)
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('renders chips in the order given, not sorted locally', () => {
    // Display order is the store's `order` field, applied server-side. The
    // component must not impose its own.
    const reversed = [...CHIPS].reverse()
    render(<SearchChips chips={reversed} query="" onSearch={() => {}} />)
    const labels = screen.getAllByRole('button').map((b) => b.textContent)
    expect(labels.map((l) => l.replace(/^\P{L}+/u, ''))).toEqual(
      reversed.map((c) => c.label)
    )
  })

  it('fires a search with the whole chip object, keyed for the dismissal ledger', () => {
    // The caller needs both the prompt (what reaches CLIP, for the search box
    // and label) and `id` (the ledger key and what /search/chip resolves), so
    // onSearch receives the chip, not a bare string.
    const onSearch = vi.fn()
    render(<SearchChips chips={CHIPS} query="" onSearch={onSearch} />)
    screen.getAllByRole('button')[0].click()
    expect(onSearch).toHaveBeenCalledWith(CHIPS[0])
  })

  it('highlights the chip whose prompt matches the active query', () => {
    const active = CHIPS[2]
    render(
      <SearchChips chips={CHIPS} query={active.query.prompts[0]} onSearch={() => {}} />
    )
    const button = screen.getByText(active.label).closest('button')
    expect(button).toHaveStyle({ borderColor: '#818cf8' })
  })

  it('highlights nothing when the search box holds something else', () => {
    render(
      <SearchChips chips={CHIPS} query="sunset over the ocean" onSearch={() => {}} />
    )
    for (const chip of CHIPS) {
      const button = screen.getByText(chip.label).closest('button')
      expect(button).not.toHaveStyle({ borderColor: '#818cf8' })
    }
  })

  it('matches on the prompt, never on the label', () => {
    // Regression guard: the label is display text and the prompt is what goes
    // to CLIP. Highlighting off the label would light the wrong chip up as
    // soon as the two diverge, which an editable chip makes easy.
    render(
      <SearchChips chips={CHIPS} query={CHIPS[0].label} onSearch={() => {}} />
    )
    const button = screen.getByText(CHIPS[0].label).closest('button')
    expect(button).not.toHaveStyle({ borderColor: '#818cf8' })
  })
})
