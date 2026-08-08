/**
 * The one time<->pixel mapping for the review-stage timeline.
 *
 * The properties under test are the ones CutTimeline's zoom/pan depends on:
 * a boundary is stored in SECONDS and zoom may only change where it is drawn,
 * so every mapping has to be exactly invertible at a given zoom, `fitZoom` has
 * to be the level where the clip exactly fills the viewport (and therefore the
 * floor you can never zoom out past), and `maxZoom` has to cap how few seconds
 * can ever fill the viewport at once (the ceiling — past this, zooming in only
 * makes panning tedious without helping you place a frame).
 */
import { describe, it, expect } from 'vitest'
import { timeToPixel, pixelToTime, fitZoom, maxZoom, clampZoom, niceTickInterval } from './timelineScale'

// Three scales that bracket real use: whole 10-minute clip squeezed into a
// panel, roughly 1:1, and frame-level zoom.
const ZOOMS = [0.5, 1, 13.7, 240, 4800]
const TIMES = [0, 0.001, 1 / 30, 0.5, 1, 12.345, 59.9999, 600]

describe('timeToPixel', () => {
  it('puts t=0 at the left edge of the track at every zoom', () => {
    // The scroll math (scrollLeft = timeToPixel(anchor) - screenX) assumes the
    // origin is anchored, not offset.
    for (const pps of ZOOMS) expect(timeToPixel(0, pps)).toBe(0)
  })

  it('is linear in time', () => {
    expect(timeToPixel(4, 100)).toBe(2 * timeToPixel(2, 100))
  })

  it('is linear in zoom', () => {
    expect(timeToPixel(3, 200)).toBe(2 * timeToPixel(3, 100))
  })

  it('preserves the order of two timestamps at every zoom', () => {
    for (const pps of ZOOMS) {
      expect(timeToPixel(2, pps)).toBeLessThan(timeToPixel(2.0001, pps))
    }
  })

  it('places a time before the clip start left of the origin', () => {
    // No clamping here on purpose: CutTimeline clamps to [0, total] at the call
    // site (`pct`), so the mapping itself stays a pure linear scale.
    expect(timeToPixel(-1, 100)).toBe(-100)
  })
})

describe('pixelToTime', () => {
  it('maps the origin back to the start of the clip', () => {
    for (const pps of ZOOMS) expect(pixelToTime(0, pps)).toBe(0)
  })

  it('is linear in pixels', () => {
    expect(pixelToTime(400, 100)).toBe(2 * pixelToTime(200, 100))
  })

  it('returns a usable time rather than Infinity for a degenerate zoom', () => {
    // A zero px/sec scale has no answer; the contract is to degrade to the
    // clip start so a stray hover can't feed Infinity/NaN into the playhead.
    for (const pps of [0, -0, -10]) {
      const t = pixelToTime(500, pps)
      expect(Number.isFinite(t)).toBe(true)
      expect(t).toBe(0)
    }
  })
})

describe('round trip', () => {
  it('returns the original timestamp when read back at the same zoom', () => {
    for (const pps of ZOOMS) {
      for (const t of TIMES) {
        expect(pixelToTime(timeToPixel(t, pps), pps)).toBeCloseTo(t, 9)
      }
    }
  })

  it('round-trips a pixel position back to the same pixel', () => {
    // The other direction: the hover tooltip converts x -> t for display while
    // the guide line stays at x, and the two must describe the same place.
    for (const pps of ZOOMS) {
      for (const x of [0, 1, 37.5, 1920]) {
        expect(timeToPixel(pixelToTime(x, pps), pps)).toBeCloseTo(x, 6)
      }
    }
  })

  it('leaves a boundary on exactly the same timestamp across a zoom change', () => {
    // The headline claim of the module: zooming can move where a region is
    // drawn but never what it means.
    const region = { start: 12.345, end: 47.5 }
    const zoomedOut = 2.5
    const zoomedIn = 900
    for (const t of [region.start, region.end]) {
      expect(pixelToTime(timeToPixel(t, zoomedOut), zoomedOut)).toBeCloseTo(t, 9)
      expect(pixelToTime(timeToPixel(t, zoomedIn), zoomedIn)).toBeCloseTo(t, 9)
    }
  })

  it('scales a region’s on-screen width by the zoom ratio, leaving its duration alone', () => {
    const region = { start: 12.345, end: 47.5 }
    const widthAt = (pps) => timeToPixel(region.end, pps) - timeToPixel(region.start, pps)
    expect(widthAt(200)).toBeCloseTo(4 * widthAt(50), 6)
    // ...and the seconds it stands for are untouched by either scale.
    expect(pixelToTime(widthAt(200), 200)).toBeCloseTo(region.end - region.start, 9)
    expect(pixelToTime(widthAt(50), 50)).toBeCloseTo(region.end - region.start, 9)
  })

  it('does NOT reproduce the time when read back at a different zoom', () => {
    // Two scales are not interchangeable, and nothing may rely on them being
    // so: a pixel is only meaningful together with the zoom it was measured at.
    const t = 12.345
    const a = 50
    const b = 200
    expect(pixelToTime(timeToPixel(t, a), b)).not.toBeCloseTo(t, 3)
    expect(pixelToTime(timeToPixel(t, a), b)).toBeCloseTo(t * (a / b), 9)
  })
})

describe('fitZoom', () => {
  it('is the zoom at which the whole clip exactly fills the viewport', () => {
    for (const [duration, width] of [[60, 800], [3.5, 1200], [612.75, 431]]) {
      expect(timeToPixel(duration, fitZoom(duration, width))).toBeCloseTo(width, 6)
    }
  })

  it('reads the far edge of the viewport back as the end of the clip', () => {
    const [duration, width] = [137.5, 940]
    expect(pixelToTime(width, fitZoom(duration, width))).toBeCloseTo(duration, 9)
  })

  it('halves when the clip is twice as long', () => {
    expect(fitZoom(120, 800)).toBeCloseTo(fitZoom(60, 800) / 2, 9)
  })

  it('doubles when the viewport is twice as wide', () => {
    expect(fitZoom(60, 1600)).toBeCloseTo(fitZoom(60, 800) * 2, 9)
  })

  it('falls back to a usable positive scale when the duration is unknown', () => {
    // Duration is 0/undefined on the very first render, before metadata lands.
    // The only real requirement is a finite positive px/sec — anything else
    // (0, Infinity, NaN) propagates into the track width and the clamp bounds.
    for (const duration of [0, -5, undefined, null, NaN]) {
      const z = fitZoom(duration, 800)
      expect(Number.isFinite(z)).toBe(true)
      expect(z).toBeGreaterThan(0)
    }
  })

  it('falls back to a usable positive scale when the viewport is not measured yet', () => {
    // viewportWidth starts at 0 and is filled in by a layout effect.
    for (const width of [0, undefined, null, NaN]) {
      const z = fitZoom(60, width)
      expect(Number.isFinite(z)).toBe(true)
      expect(z).toBeGreaterThan(0)
    }
  })
})

describe('maxZoom', () => {
  it('caps the viewport at MIN_VISIBLE_SECONDS of footage, whatever the viewport width', () => {
    // The defining contract: at max zoom, exactly 3 seconds fill the viewport
    // — not a fixed px/sec, not a per-frame budget.
    for (const width of [400, 800, 1920]) {
      expect(timeToPixel(3, maxZoom(width))).toBeCloseTo(width, 6)
    }
  })

  it('needs more px/sec for a wider viewport', () => {
    expect(maxZoom(1600)).toBeCloseTo(2 * maxZoom(800), 9)
  })

  it('falls back to a usable positive scale when the viewport is not measured yet', () => {
    for (const width of [0, undefined, null, NaN]) {
      const z = maxZoom(width)
      expect(Number.isFinite(z)).toBe(true)
      expect(z).toBeGreaterThan(0)
    }
  })
})

describe('clampZoom', () => {
  // A one-minute clip in an 800px panel: fit = 13.33 px/sec, max (3s window) = 266.67 px/sec.
  const DURATION = 60
  const WIDTH = 800

  it('passes a zoom that is already in range through untouched', () => {
    for (const candidate of [14, 100, 266]) {
      expect(clampZoom(candidate, DURATION, WIDTH)).toBe(candidate)
    }
  })

  it('refuses to zoom out past seeing the whole clip', () => {
    const fit = fitZoom(DURATION, WIDTH)
    for (const candidate of [0, 1, fit - 0.001, -50]) {
      expect(clampZoom(candidate, DURATION, WIDTH)).toBe(fit)
    }
  })

  it('never lets the track end up narrower than the viewport', () => {
    // The same rule stated the way the user sees it — no dead space to the
    // right of the clip.
    for (const candidate of [0.01, 1, 5, 13, 13.33, 20, 1e6]) {
      const z = clampZoom(candidate, DURATION, WIDTH)
      expect(timeToPixel(DURATION, z)).toBeGreaterThanOrEqual(WIDTH - 1e-6)
    }
  })

  it('clamps a runaway zoom down to the max useful zoom (3s window)', () => {
    const max = maxZoom(WIDTH)
    for (const candidate of [max + 0.001, 5000, 1e9]) {
      expect(clampZoom(candidate, DURATION, WIDTH)).toBe(max)
    }
    // ...and at that ceiling, exactly 3 seconds fill the viewport.
    expect(timeToPixel(3, clampZoom(1e9, DURATION, WIDTH))).toBeCloseTo(WIDTH, 6)
  })

  it('lets the boundary values themselves through unchanged', () => {
    const fit = fitZoom(DURATION, WIDTH)
    const max = maxZoom(WIDTH)
    expect(clampZoom(fit, DURATION, WIDTH)).toBe(fit)
    expect(clampZoom(max, DURATION, WIDTH)).toBe(max)
  })

  it('is idempotent', () => {
    for (const candidate of [0, 1, 14, 100, 1e6]) {
      const once = clampZoom(candidate, DURATION, WIDTH)
      expect(clampZoom(once, DURATION, WIDTH)).toBe(once)
    }
  })

  it('preserves the order of two candidate zooms', () => {
    const candidates = [0, 5, 13, 50, 266, 400, 5000]
    const clamped = candidates.map((c) => clampZoom(c, DURATION, WIDTH))
    for (let i = 1; i < clamped.length; i += 1) {
      expect(clamped[i]).toBeGreaterThanOrEqual(clamped[i - 1])
    }
  })

  it('needs a higher ceiling for a wider viewport', () => {
    expect(clampZoom(1e6, DURATION, 1600))
      .toBeGreaterThan(clampZoom(1e6, DURATION, 800))
  })

  describe('when the clip is so short that fit zoom already exceeds max zoom', () => {
    // 0.5s in a 1200px panel: fit = 2400 px/sec, well past the 3s-window ceiling of 400.
    const SHORT = 0.5
    const WIDE = 1200

    it('does not produce an inverted range', () => {
      const fit = fitZoom(SHORT, WIDE)
      expect(fit).toBeGreaterThan(maxZoom(WIDE))
      for (const candidate of [0, 100, 400, 2400, 1e6]) {
        const z = clampZoom(candidate, SHORT, WIDE)
        expect(Number.isFinite(z)).toBe(true)
        expect(z).toBeGreaterThanOrEqual(fit)
      }
    })

    it('pins every candidate to fit zoom — seeing the whole clip wins over the ceiling', () => {
      const fit = fitZoom(SHORT, WIDE)
      expect(clampZoom(10, SHORT, WIDE)).toBe(fit)
      expect(clampZoom(1e6, SHORT, WIDE)).toBe(fit)
      // ...and the whole clip still exactly fills the viewport at that zoom.
      expect(timeToPixel(SHORT, clampZoom(1e6, SHORT, WIDE))).toBeCloseTo(WIDE, 6)
    })
  })

  it('still returns a finite positive zoom before the viewport is measured', () => {
    // First paint: viewportWidth is 0 until the layout effect runs, and a wheel
    // event can land in that window.
    for (const candidate of [0, 50, 1e6]) {
      const z = clampZoom(candidate, DURATION, 0)
      expect(Number.isFinite(z)).toBe(true)
      expect(z).toBeGreaterThan(0)
    }
  })
})

describe('niceTickInterval', () => {
  it('never lets adjacent tick labels crowd closer than the requested gap', () => {
    for (const pps of [0.5, 1, 13.7, 50, 240, 4800]) {
      const interval = niceTickInterval(pps, 70)
      expect(timeToPixel(interval, pps)).toBeGreaterThanOrEqual(70 - 1e-9)
    }
  })

  it('picks a smaller interval as you zoom in', () => {
    const zoomedOut = niceTickInterval(5, 70)
    const zoomedIn = niceTickInterval(500, 70)
    expect(zoomedIn).toBeLessThan(zoomedOut)
  })

  it('picks the smallest nice interval that satisfies the gap, not an oversized one', () => {
    // At 100px/sec with a 70px minimum gap, 1-second ticks (100px apart)
    // already satisfy the gap — it shouldn't skip up to 2s or 5s.
    expect(niceTickInterval(100, 70)).toBe(1)
  })

  it('falls back to its largest interval rather than returning nothing for a huge minimum gap', () => {
    expect(niceTickInterval(1, 100000)).toBe(3600)
  })

  it('respects a custom minimum gap', () => {
    expect(niceTickInterval(100, 10)).toBeLessThan(niceTickInterval(100, 500))
  })
})
