import { describe, it, expect } from 'vitest'
import { fmtDur, formatBytes } from './format'

describe('fmtDur', () => {
  it('formats seconds as m:ss.s', () => {
    expect(fmtDur(0)).toBe('0:00.0')
    expect(fmtDur(9.24)).toBe('0:09.2')
    expect(fmtDur(65.5)).toBe('1:05.5')
  })

  it('pads the seconds so the width never jumps mid-playback', () => {
    // The readout sits next to a moving playhead; an unpadded "1:5.0" would
    // shift every glyph after it once a second.
    expect(fmtDur(61)).toBe('1:01.0')
    expect(fmtDur(3599.9)).toBe('59:59.9')
  })

  it('keeps counting minutes past an hour rather than rolling over', () => {
    expect(fmtDur(3600)).toBe('60:00.0')
  })
})

describe('formatBytes', () => {
  it('shows raw bytes below a kilobyte', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1)).toBe('1 B')
    expect(formatBytes(1023)).toBe('1023 B')
  })

  it('switches unit exactly at the boundary', () => {
    expect(formatBytes(1024)).toBe('1 KB')
    expect(formatBytes(1024 ** 2)).toBe('1.0 MB')
    expect(formatBytes(1024 ** 3)).toBe('1.0 GB')
    expect(formatBytes(1024 ** 4)).toBe('1.0 TB')
  })

  it('drops the decimal once the number is big enough not to need it', () => {
    // The precision rule: one decimal while it adds information, none above 10.
    expect(formatBytes(1.5 * 1024 ** 2)).toBe('1.5 MB')
    expect(formatBytes(9.9 * 1024 ** 2)).toBe('9.9 MB')
    expect(formatBytes(12 * 1024 ** 2)).toBe('12 MB')
  })

  it('never shows a decimal for kilobytes', () => {
    expect(formatBytes(1536)).toBe('2 KB')
  })

  it('stops at terabytes rather than inventing a unit', () => {
    expect(formatBytes(5000 * 1024 ** 4)).toMatch(/TB$/)
  })

  it('treats junk as zero instead of rendering NaN in the UI', () => {
    // The savings payload can arrive incomplete; "NaN MB" on screen is worse
    // than "0 B".
    expect(formatBytes(undefined)).toBe('0 B')
    expect(formatBytes(null)).toBe('0 B')
    expect(formatBytes(NaN)).toBe('0 B')
    expect(formatBytes('not a number')).toBe('0 B')
  })

  it('accepts a numeric string', () => {
    expect(formatBytes('2048')).toBe('2 KB')
  })
})
