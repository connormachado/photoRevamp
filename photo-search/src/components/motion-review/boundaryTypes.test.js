/**
 * Edit-boundary registry — display half.
 *
 * This file is one of a declared mirror pair: `backend/edit_boundaries.py` owns
 * the export half, and the two are keyed by the same type-id strings. Where a
 * value exists on both sides (defaultParams, the speed clamps, what a type
 * contributes to the output duration) a divergence means the preview and the
 * render disagree — so several tests here pin the shared contract, not just the
 * local behaviour.
 */
import { describe, it, expect } from 'vitest'
import {
  BOUNDARY_TYPES,
  TYPE_LIST,
  DEFAULT_TYPE_ID,
  SPEED_MIN_MAGNITUDE,
  SPEED_MAX_MAGNITUDE,
  clampMagnitude,
  effectiveSpeed,
  getType,
} from './boundaryTypes'

describe('clampMagnitude', () => {
  it('keeps a magnitude inside the allowed range', () => {
    expect(clampMagnitude(1)).toBe(1)
    expect(clampMagnitude(2.5)).toBe(2.5)
    expect(clampMagnitude(20)).toBe(20)
  })

  it('floors at 1 rather than crossing into "slower"', () => {
    // Direction is a separate toggle; a magnitude below 1 would mean the same
    // thing twice and the step-down would silently invert.
    expect(clampMagnitude(0.5)).toBe(SPEED_MIN_MAGNITUDE)
    expect(clampMagnitude(0)).toBe(SPEED_MIN_MAGNITUDE)
    expect(clampMagnitude(-4)).toBe(SPEED_MIN_MAGNITUDE)
  })

  it('caps at the maximum', () => {
    expect(clampMagnitude(9999)).toBe(SPEED_MAX_MAGNITUDE)
    expect(clampMagnitude(Infinity)).toBe(2)   // not finite -> default
  })

  it('falls back to the default for anything unparseable', () => {
    // A half-typed value in the magnitude input must not produce NaN× on screen.
    expect(clampMagnitude(undefined)).toBe(2)
    expect(clampMagnitude(NaN)).toBe(2)
    expect(clampMagnitude('abc')).toBe(2)
    expect(clampMagnitude({})).toBe(2)
  })

  it('clamps values JS coerces to 0 rather than calling them unparseable', () => {
    // Number(null) and Number('') are both 0 — finite, so they take the clamp
    // path and land on 1 (a no-op speed), not the 2x default. Either outcome is
    // harmless here; pinning it so a future refactor has to be deliberate.
    expect(clampMagnitude(null)).toBe(SPEED_MIN_MAGNITUDE)
    expect(clampMagnitude('')).toBe(SPEED_MIN_MAGNITUDE)
  })

  it('accepts a numeric string from the text input', () => {
    expect(clampMagnitude('3.5')).toBe(3.5)
  })
})

describe('effectiveSpeed', () => {
  it('defaults to 2x up', () => {
    expect(effectiveSpeed({})).toBe(2)
  })

  it('is the magnitude when speeding up', () => {
    expect(effectiveSpeed({ direction: 'up', magnitude: 4 })).toBe(4)
  })

  it('is the reciprocal when slowing down', () => {
    expect(effectiveSpeed({ direction: 'down', magnitude: 4 })).toBe(0.25)
  })

  it('treats a missing direction as up', () => {
    expect(effectiveSpeed({ magnitude: 3 })).toBe(3)
  })

  it('survives null params without throwing', () => {
    // Regions written before "speed" existed carry no params at all.
    expect(effectiveSpeed(null)).toBe(2)
    expect(effectiveSpeed(undefined)).toBe(2)
  })

  it('never divides by zero, because the magnitude floors at 1', () => {
    expect(effectiveSpeed({ direction: 'down', magnitude: 0 })).toBe(1)
    expect(Number.isFinite(effectiveSpeed({ direction: 'down', magnitude: -5 }))).toBe(true)
  })

  it('is symmetric at the extremes the UI allows', () => {
    expect(effectiveSpeed({ direction: 'up', magnitude: 20 })).toBe(20)
    expect(effectiveSpeed({ direction: 'down', magnitude: 20 })).toBe(0.05)
  })
})

describe('getType', () => {
  it('resolves a registered id', () => {
    expect(getType('cut').id).toBe('cut')
    expect(getType('speed').id).toBe('speed')
  })

  it('falls back to the default for an unknown id instead of throwing', () => {
    // A stale or foreign region must still render rather than blowing up the
    // whole timeline. NOTE: the fallback is `cut`, so unknown-typed footage is
    // previewed as REMOVED — matching what the backend plan builder does with
    // the same input.
    expect(getType('from-the-future').id).toBe(DEFAULT_TYPE_ID)
    expect(getType(undefined).id).toBe(DEFAULT_TYPE_ID)
    expect(getType(null).id).toBe(DEFAULT_TYPE_ID)
    expect(getType('').id).toBe(DEFAULT_TYPE_ID)
  })
})

describe('the cut type', () => {
  const cut = BOUNDARY_TYPES.cut

  it('removes footage and contributes nothing to the output', () => {
    expect(cut.removesFootage).toBe(true)
    expect(cut.outputDuration({ start: 10, end: 20 })).toBe(0)
  })

  it('becomes no pieces at all', () => {
    expect(cut.toPieces({ start: 10, end: 20 })).toEqual([])
  })
})

describe('the speed type', () => {
  const speed = BOUNDARY_TYPES.speed

  it('keeps its footage', () => {
    expect(speed.removesFootage).toBe(false)
  })

  it('reports the retimed length, not the source length', () => {
    expect(speed.outputDuration({
      start: 10, end: 20, params: { direction: 'up', magnitude: 2 },
    })).toBe(5)
  })

  it('reports a longer output when slowed down', () => {
    expect(speed.outputDuration({
      start: 10, end: 20, params: { direction: 'down', magnitude: 2 },
    })).toBe(20)
  })

  it('becomes one piece spanning the same source range', () => {
    const [piece] = speed.toPieces({
      start: 10, end: 20, params: { direction: 'up', magnitude: 4 },
    })
    expect(piece.start).toBe(10)
    expect(piece.end).toBe(20)
    expect(piece.speed).toBe(4)
  })

  it('agrees with its own toPieces about the output length', () => {
    // outputDuration exists so the header can avoid building a plan; if the two
    // ever disagree the header and the preview show different numbers.
    const region = { start: 10, end: 20, params: { direction: 'up', magnitude: 2.5 } }
    const fromPieces = speed.toPieces(region)
      .reduce((acc, p) => acc + (p.end - p.start) / (p.speed || 1), 0)
    expect(fromPieces).toBeCloseTo(speed.outputDuration(region), 9)
  })
})

describe('registry integrity', () => {
  it('every type is keyed by its own id', () => {
    // The id string is the contract with the backend registry; a key/id mismatch
    // would silently route a region to the wrong hook.
    for (const [key, type] of Object.entries(BOUNDARY_TYPES)) {
      expect(type.id).toBe(key)
    }
  })

  it('every type implements the full hook surface the timeline calls', () => {
    for (const type of TYPE_LIST) {
      expect(typeof type.toPieces).toBe('function')
      expect(typeof type.outputDuration).toBe('function')
      expect(typeof type.describe).toBe('function')
      expect(typeof type.removesFootage).toBe('boolean')
      expect(type.defaultParams).toBeTypeOf('object')
      expect(type.minWidthFrames).toBeGreaterThan(0)
      expect(type.defaultLengthSeconds).toBeGreaterThan(0)
    }
  })

  it('the default type is registered', () => {
    expect(BOUNDARY_TYPES[DEFAULT_TYPE_ID]).toBeDefined()
  })

  it('describe never throws on a region with no params', () => {
    // Legacy regions carry no params; the toolbar calls describe on all of them.
    for (const type of TYPE_LIST) {
      expect(() => type.describe({ start: 1, end: 2 })).not.toThrow()
    }
  })

  it("speed's default params match the backend registry", () => {
    // backend/edit_boundaries.py: default_params={"direction": "up", "magnitude": 2.0}
    expect(BOUNDARY_TYPES.speed.defaultParams).toEqual({ direction: 'up', magnitude: 2 })
  })

  it('the speed clamps match the backend registry', () => {
    // backend/edit_boundaries.py: SPEED_MIN_MAGNITUDE = 1.0, SPEED_MAX_MAGNITUDE = 20.0
    expect(SPEED_MIN_MAGNITUDE).toBe(1)
    expect(SPEED_MAX_MAGNITUDE).toBe(20)
  })
})
