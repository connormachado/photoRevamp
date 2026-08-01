import { describe, it, expect } from 'vitest'
import {
  complementSegments,
  sumDurations,
  indexForTime,
  segmentsKey,
  ratesKey,
} from './segments'

describe('complementSegments', () => {
  it('returns the whole video when nothing is cut', () => {
    expect(complementSegments([], 60)).toEqual([{ start: 0, end: 60 }])
    expect(complementSegments(null, 60)).toEqual([{ start: 0, end: 60 }])
  })

  it('returns nothing when everything is cut', () => {
    expect(complementSegments([{ start: 0, end: 60 }], 60)).toEqual([])
  })

  it('leaves the gaps around a middle cut', () => {
    expect(complementSegments([{ start: 20, end: 30 }], 60)).toEqual([
      { start: 0, end: 20 },
      { start: 30, end: 60 },
    ])
  })

  it('emits no leading keep when a cut starts at zero', () => {
    expect(complementSegments([{ start: 0, end: 10 }], 60)).toEqual([
      { start: 10, end: 60 },
    ])
  })

  it('emits no trailing keep when a cut runs to the end', () => {
    expect(complementSegments([{ start: 50, end: 60 }], 60)).toEqual([
      { start: 0, end: 50 },
    ])
  })

  it('handles cuts arriving out of order', () => {
    const keeps = complementSegments(
      [{ start: 40, end: 50 }, { start: 10, end: 20 }], 60)
    expect(keeps).toEqual([
      { start: 0, end: 10 },
      { start: 20, end: 40 },
      { start: 50, end: 60 },
    ])
  })

  it('does not mutate the caller array', () => {
    // These arrays are rebuilt every render and shared with React state.
    const cuts = [{ start: 40, end: 50 }, { start: 10, end: 20 }]
    complementSegments(cuts, 60)
    expect(cuts[0].start).toBe(40)
  })

  it('absorbs overlapping cuts instead of emitting a negative keep', () => {
    expect(complementSegments(
      [{ start: 10, end: 30 }, { start: 20, end: 40 }], 60)).toEqual([
      { start: 0, end: 10 },
      { start: 40, end: 60 },
    ])
  })

  it('swallows a nested cut', () => {
    expect(complementSegments(
      [{ start: 10, end: 40 }, { start: 20, end: 25 }], 60)).toEqual([
      { start: 0, end: 10 },
      { start: 40, end: 60 },
    ])
  })

  it('clamps cuts that run outside the video', () => {
    expect(complementSegments([{ start: -5, end: 10 }], 60)).toEqual([
      { start: 10, end: 60 },
    ])
    expect(complementSegments([{ start: 50, end: 999 }], 60)).toEqual([
      { start: 0, end: 50 },
    ])
  })

  it('does not emit sub-millisecond slivers as playable segments', () => {
    const keeps = complementSegments(
      [{ start: 0, end: 20 }, { start: 20.0005, end: 60 }], 60)
    expect(keeps).toEqual([])
  })
})

describe('sumDurations', () => {
  it('is zero for nothing', () => {
    expect(sumDurations([])).toBe(0)
    expect(sumDurations(null)).toBe(0)
  })

  it('adds the spans', () => {
    expect(sumDurations([{ start: 0, end: 10 }, { start: 20, end: 35 }])).toBe(25)
  })
})

describe('indexForTime', () => {
  const segs = [
    { start: 0, end: 10 },
    { start: 20, end: 30 },
    { start: 40, end: 50 },
  ]

  it('finds the segment a time sits inside', () => {
    expect(indexForTime(segs, 5)).toBe(0)
    expect(indexForTime(segs, 25)).toBe(1)
    expect(indexForTime(segs, 45)).toBe(2)
  })

  it('snaps a time inside a gap forward to the next segment', () => {
    // A gap is footage this panel does not play, so the next thing it WILL
    // show is the useful answer.
    expect(indexForTime(segs, 15)).toBe(1)
    expect(indexForTime(segs, 35)).toBe(2)
  })

  it('treats a segment boundary as the start of the next segment', () => {
    expect(indexForTime(segs, 10)).toBe(1)
  })

  it('clamps a time past the end to the last segment', () => {
    expect(indexForTime(segs, 999)).toBe(2)
  })

  it('returns a usable index for an empty list rather than -1', () => {
    // -1 would index past the end of the array in the caller.
    expect(indexForTime([], 5)).toBe(0)
    expect(indexForTime(null, 5)).toBe(0)
  })
})

describe('segmentsKey and ratesKey', () => {
  const segs = [{ start: 0, end: 10, speed: 1 }, { start: 20, end: 30, speed: 2 }]

  it('are stable across rebuilt arrays with identical content', () => {
    // The whole point: effects must key off content, not array identity, or
    // every render restarts playback.
    const rebuilt = segs.map((s) => ({ ...s }))
    expect(segmentsKey(rebuilt)).toBe(segmentsKey(segs))
    expect(ratesKey(rebuilt)).toBe(ratesKey(segs))
  })

  it('changes when a boundary moves', () => {
    const moved = [{ start: 0, end: 11, speed: 1 }, { start: 20, end: 30, speed: 2 }]
    expect(segmentsKey(moved)).not.toBe(segmentsKey(segs))
  })

  it('ignores a speed change so nudging a magnitude does not restart playback', () => {
    // Bounds and rates are keyed separately on purpose: a speed nudge should
    // change the playback rate, not yank the panel back to the start.
    const faster = [{ start: 0, end: 10, speed: 1 }, { start: 20, end: 30, speed: 4 }]
    expect(segmentsKey(faster)).toBe(segmentsKey(segs))
    expect(ratesKey(faster)).not.toBe(ratesKey(segs))
  })

  it('ignores sub-millisecond float drift in bounds', () => {
    const drifted = [{ start: 0.00001, end: 10, speed: 1 },
                     { start: 20, end: 30, speed: 2 }]
    expect(segmentsKey(drifted)).toBe(segmentsKey(segs))
  })

  it('treats a missing speed as 1x', () => {
    expect(ratesKey([{ start: 0, end: 10 }])).toBe(ratesKey([{ start: 0, end: 10, speed: 1 }]))
  })

  it('handles empty input', () => {
    expect(segmentsKey([])).toBe('')
    expect(ratesKey(null)).toBe('')
  })
})
