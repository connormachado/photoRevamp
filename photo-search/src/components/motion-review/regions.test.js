/**
 * Region math — the display-side mirror of backend/edit_boundaries.py.
 *
 * `buildPlan` here and `build_plan` there must agree, or the preview shows
 * something the export will not produce. The scenarios below deliberately match
 * the ones in tests/test_edit_boundaries.py so a divergence shows up as one side
 * going red.
 */
import { describe, it, expect } from 'vitest'
import {
  makeRegion,
  regionsFromCuts,
  regionsToCuts,
  sortRegions,
  buildPlan,
  outputDuration,
  regionsEqual,
  addRegionAt,
  removeRegion,
} from './regions'

const cut = (start, end) => ({ id: `c${start}`, type: 'cut', start, end, params: {} })
const speed = (start, end, magnitude = 2, direction = 'up') => ({
  id: `s${start}`, type: 'speed', start, end, params: { direction, magnitude },
})

describe('makeRegion', () => {
  it('seeds the type defaults', () => {
    expect(makeRegion('speed', 1, 5).params).toEqual({ direction: 'up', magnitude: 2 })
    expect(makeRegion('cut', 1, 5).params).toEqual({})
  })

  it('gives every region a distinct id', () => {
    // The id is the React key and the drag handle target; a collision makes two
    // regions impossible to tell apart.
    const ids = new Set(Array.from({ length: 50 }, () => makeRegion('cut', 0, 1).id))
    expect(ids.size).toBe(50)
  })

  it('falls back to the default type for an unknown id', () => {
    expect(makeRegion('nonsense', 0, 1).type).toBe('cut')
  })
})

describe('buildPlan', () => {
  it('keeps the whole video when there are no regions', () => {
    expect(buildPlan([], 60)).toEqual([{ start: 0, end: 60, speed: 1 }])
    expect(buildPlan(null, 60)).toEqual([{ start: 0, end: 60, speed: 1 }])
  })

  it('produces nothing for a zero-length video', () => {
    expect(buildPlan([], 0)).toEqual([])
  })

  it('leaves the two gaps around a middle cut', () => {
    expect(buildPlan([cut(20, 30)], 60)).toEqual([
      { start: 0, end: 20, speed: 1 },
      { start: 30, end: 60, speed: 1 },
    ])
  })

  it('emits no leading piece when a cut starts at zero', () => {
    expect(buildPlan([cut(0, 10)], 60)).toEqual([{ start: 10, end: 60, speed: 1 }])
  })

  it('emits no trailing piece when a cut runs to the end', () => {
    expect(buildPlan([cut(50, 60)], 60)).toEqual([{ start: 0, end: 50, speed: 1 }])
  })

  it('produces nothing when a cut spans the whole video', () => {
    expect(buildPlan([cut(0, 60)], 60)).toEqual([])
  })

  it('walks regions in time order regardless of input order', () => {
    const plan = buildPlan([cut(40, 50), cut(10, 20)], 60)
    expect(plan.map((p) => [p.start, p.end])).toEqual([[0, 10], [20, 40], [50, 60]])
  })

  it('does not emit sub-millisecond gaps as pieces', () => {
    const plan = buildPlan([cut(0, 20), cut(20.0005, 40)], 60)
    expect(plan.map((p) => [p.start, p.end])).toEqual([[40, 60]])
  })

  it('clamps regions that run outside the video', () => {
    expect(buildPlan([cut(-5, 999)], 60)).toEqual([])
  })

  it('keeps a speed region’s footage but marks its rate', () => {
    const plan = buildPlan([speed(20, 40, 2)], 60)
    expect(plan).toEqual([
      { start: 0, end: 20, speed: 1 },
      { start: 20, end: 40, speed: 2 },
      { start: 40, end: 60, speed: 1 },
    ])
  })

  it('marks a slowed region with a fractional rate', () => {
    const [piece] = buildPlan([speed(0, 10, 2, 'down')], 10)
    expect(piece.speed).toBe(0.5)
  })

  it('gives every piece an explicit speed so callers never read undefined', () => {
    for (const p of buildPlan([cut(10, 20), speed(30, 40)], 60)) {
      expect(typeof p.speed).toBe('number')
    }
  })

  it('composes cuts and speed regions', () => {
    const plan = buildPlan([cut(10, 20), speed(30, 40, 2)], 60)
    expect(plan.map((p) => [p.start, p.end, p.speed])).toEqual([
      [0, 10, 1], [20, 30, 1], [30, 40, 2], [40, 60, 1],
    ])
  })

  it('does not mutate the regions it is given', () => {
    // buildPlan runs on every render against React state.
    const regions = [cut(20, 30)]
    buildPlan(regions, 60)
    expect(regions).toEqual([cut(20, 30)])
  })
})

describe('outputDuration', () => {
  it('is the full length when nothing is edited', () => {
    expect(outputDuration([], 60)).toBe(60)
  })

  it('subtracts cut footage', () => {
    expect(outputDuration([cut(20, 30)], 60)).toBe(50)
  })

  it('is zero when everything is cut, never negative', () => {
    expect(outputDuration([cut(0, 60)], 60)).toBe(0)
  })

  it('shortens for a sped-up region', () => {
    // 20 kept + (20 / 2) + 20 = 50
    expect(outputDuration([speed(20, 40, 2)], 60)).toBe(50)
  })

  it('lengthens for a slowed region', () => {
    // 20 + (20 * 2) + 20 = 80
    expect(outputDuration([speed(20, 40, 2, 'down')], 60)).toBe(80)
  })

  it('agrees with the plan it is derived from', () => {
    // One source of truth: the header and the preview panel used to compute this
    // separately and came to disagree about speed regions.
    const regions = [cut(5, 10), speed(20, 40, 4), cut(50, 55)]
    const fromPlan = buildPlan(regions, 60)
      .reduce((acc, p) => acc + (p.end - p.start) / (p.speed || 1), 0)
    expect(outputDuration(regions, 60)).toBeCloseTo(fromPlan, 9)
  })
})

describe('regionsEqual', () => {
  it('ignores ids', () => {
    expect(regionsEqual(
      [{ id: 'a', type: 'cut', start: 1, end: 2, params: {} }],
      [{ id: 'b', type: 'cut', start: 1, end: 2, params: {} }],
    )).toBe(true)
  })

  it('ignores ordering', () => {
    expect(regionsEqual([cut(30, 40), cut(10, 20)], [cut(10, 20), cut(30, 40)])).toBe(true)
  })

  it('tolerates sub-millisecond drift from a drag', () => {
    // Float noise must not light up the "edited" badge on an untouched video.
    expect(regionsEqual(
      [{ type: 'cut', start: 1.00001, end: 2, params: {} }],
      [{ type: 'cut', start: 1.00002, end: 2, params: {} }],
    )).toBe(true)
  })

  it('notices a real boundary move', () => {
    expect(regionsEqual([cut(1, 2)], [cut(1, 3)])).toBe(false)
  })

  it('notices a type change', () => {
    expect(regionsEqual(
      [{ type: 'cut', start: 1, end: 2, params: {} }],
      [{ type: 'speed', start: 1, end: 2, params: {} }],
    )).toBe(false)
  })

  it('notices a params change', () => {
    expect(regionsEqual([speed(1, 2, 2)], [speed(1, 2, 4)])).toBe(false)
  })

  it('notices an added or removed region', () => {
    expect(regionsEqual([cut(1, 2)], [])).toBe(false)
    expect(regionsEqual([], [cut(1, 2)])).toBe(false)
  })

  it('treats two empty lists as equal', () => {
    expect(regionsEqual([], [])).toBe(true)
    expect(regionsEqual(null, [])).toBe(true)
  })

  it('does not mutate either list', () => {
    const a = [cut(30, 40), cut(10, 20)]
    regionsEqual(a, [cut(10, 20), cut(30, 40)])
    expect(a[0].start).toBe(30)
  })
})

describe('cut-list interop', () => {
  it('upgrades a legacy cut list into cut regions', () => {
    const [region] = regionsFromCuts([{ start: 5, end: 9 }])
    expect(region.type).toBe('cut')
    expect([region.start, region.end]).toEqual([5, 9])
    expect(region.id).toBeTruthy()
  })

  it('handles an empty or missing cut list', () => {
    expect(regionsFromCuts([])).toEqual([])
    expect(regionsFromCuts(null)).toEqual([])
  })

  it('reports only footage-removing regions as cuts', () => {
    expect(regionsToCuts([cut(10, 20), speed(30, 40)])).toEqual([{ start: 10, end: 20 }])
  })

  it('sorts the cuts it reports', () => {
    expect(regionsToCuts([cut(40, 50), cut(10, 20)])).toEqual([
      { start: 10, end: 20 }, { start: 40, end: 50 },
    ])
  })

  it('does not mutate the region list while sorting', () => {
    const regions = [cut(40, 50), cut(10, 20)]
    regionsToCuts(regions)
    expect(regions[0].start).toBe(40)
  })

  it('survives a round trip for cut-only regions', () => {
    const original = [cut(10, 20), cut(40, 50)]
    expect(regionsEqual(original, regionsFromCuts(regionsToCuts(original)))).toBe(true)
  })
})

describe('sortRegions', () => {
  it('orders by start without mutating the input', () => {
    const regions = [cut(30, 40), cut(10, 20)]
    expect(sortRegions(regions).map((r) => r.start)).toEqual([10, 30])
    expect(regions.map((r) => r.start)).toEqual([30, 10])
  })

  it('handles empty input', () => {
    expect(sortRegions(null)).toEqual([])
  })
})

describe('removeRegion', () => {
  it('drops the region with the given id', () => {
    const regions = [cut(10, 20), cut(30, 40)]
    expect(removeRegion(regions, 'c10')).toEqual([cut(30, 40)])
  })

  it('is a no-op for an unknown id', () => {
    const regions = [cut(10, 20)]
    expect(removeRegion(regions, 'nope')).toEqual(regions)
  })

  it('does not mutate the input', () => {
    const regions = [cut(10, 20), cut(30, 40)]
    removeRegion(regions, 'c10')
    expect(regions).toHaveLength(2)
  })

  it('handles empty input', () => {
    expect(removeRegion(null, 'x')).toEqual([])
  })
})

describe('addRegionAt', () => {
  it('adds a region of the default length in open space', () => {
    const out = addRegionAt([], 'cut', 10, 60)
    expect(out).toHaveLength(1)
    expect(out[0].start).toBe(10)
    expect(out[0].end).toBeCloseTo(11.5)   // cut's defaultLengthSeconds
  })

  it('uses the type’s own default length', () => {
    const [region] = addRegionAt([], 'speed', 10, 60)
    expect(region.end - region.start).toBeCloseTo(3)   // speed is wider
  })

  it('returns the list sorted so the timeline stays ordered', () => {
    const out = addRegionAt([cut(40, 50)], 'cut', 10, 60)
    expect(out.map((r) => r.start)).toEqual([10, 40])
  })

  it('does not mutate the existing list', () => {
    const regions = [cut(40, 50)]
    addRegionAt(regions, 'cut', 10, 60)
    expect(regions).toHaveLength(1)
  })

  it('shrinks the new region to fit against the next one', () => {
    const out = addRegionAt([cut(10, 20)], 'cut', 9.5, 60)
    expect(out[0].end).toBeCloseTo(10)
    expect(out[0].start).toBeCloseTo(9.5)
  })

  it('grows leftwards when there is no room to the right', () => {
    // Running into the next region must not produce a 0.01s sliver.
    const [region] = addRegionAt([cut(10, 20)], 'cut', 9.99, 60)
    expect(region.end).toBeCloseTo(10)
    expect(region.end - region.start).toBeCloseTo(1.5)
  })

  it('stops growing left at the previous region', () => {
    const out = addRegionAt([cut(0, 9), cut(10, 20)], 'cut', 9.99, 60)
    const added = out.find((r) => r.start >= 9 && r.end <= 10)
    expect(added.start).toBeGreaterThanOrEqual(9)
  })

  it('clamps the new region to the end of the video', () => {
    const [region] = addRegionAt([], 'cut', 59.5, 60)
    expect(region.end).toBeLessThanOrEqual(60)
  })

  it('scales the minimum width by the frame rate', () => {
    // minWidthFrames / fps — a slower source allows a wider minimum.
    const tight = [cut(0, 10), cut(10.03, 20)]
    expect(addRegionAt(tight, 'cut', 10.01, 60, 30)).toBeNull()   // 2/30 = 0.067 > 0.03
    expect(addRegionAt(tight, 'cut', 10.01, 60, 120)).not.toBeNull() // 2/120 = 0.017 < 0.03
  })

  it('defaults to 30fps when none is supplied', () => {
    expect(addRegionAt([], 'cut', 10, 60, 0)).not.toBeNull()
  })

  describe('when there is no room', () => {
    it('refuses to add inside an existing region', () => {
      expect(addRegionAt([cut(10, 20)], 'cut', 15, 60)).toBeNull()
    })

    it('refuses at a region boundary', () => {
      expect(addRegionAt([cut(10, 20)], 'cut', 10, 60)).toBeNull()
      expect(addRegionAt([cut(10, 20)], 'cut', 20, 60)).toBeNull()
    })

    it('refuses when the gap is narrower than the minimum width', () => {
      expect(addRegionAt([cut(0, 10), cut(10.03, 20)], 'cut', 10.01, 60)).toBeNull()
    })

    /**
     * FLAG for review — doc/behaviour mismatch, not a crash.
     *
     * regions.js:96 says "Returns the unchanged list when there is no room", but
     * every no-room path returns `null` (lines 99, 105, 113). Callers that trust
     * the comment and do `setRegions(addRegionAt(...))` would blank the timeline
     * instead of leaving it alone.
     *
     * The current callers do handle null, so this is a latent trap rather than a
     * live bug — but the comment is wrong either way. This test pins the ACTUAL
     * contract (null) so the fix is a deliberate choice: either correct the
     * comment, or change the return and update every caller.
     */
    it('signals "no room" with null, not with the unchanged list', () => {
      const regions = [cut(10, 20)]
      const result = addRegionAt(regions, 'cut', 15, 60)
      expect(result).toBeNull()
      expect(result).not.toEqual(regions)
    })
  })
})
