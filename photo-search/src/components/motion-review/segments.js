// Pure segment math shared across the review room (mirrors the backend so the
// live preview matches what gets persisted).
//
// Segments are the OLD {start, end} shape, still used for the preview panels
// (which play a list of spans). The editable timeline speaks regions now — see
// ./regions.js and ./boundaryTypes.js.

// Keep segments = the gaps between cuts over [0, duration].
export function complementSegments(cuts, duration) {
  const keeps = [];
  let cursor = 0;
  const sorted = [...(cuts || [])].sort((a, b) => a.start - b.start);
  for (const seg of sorted) {
    const s = Math.max(0, seg.start);
    const e = Math.min(duration, seg.end);
    if (s > cursor + 1e-3) keeps.push({ start: cursor, end: s });
    cursor = Math.max(cursor, e);
  }
  if (cursor < duration - 1e-3) keeps.push({ start: cursor, end: duration });
  return keeps;
}

export function sumDurations(segs) {
  return (segs || []).reduce((acc, s) => acc + (s.end - s.start), 0);
}

// Which segment a timeline time lands in. A time inside a GAP snaps forward to
// the next segment, because a gap is footage this panel doesn't play — landing
// on the next thing it will actually show is the useful answer.
export function indexForTime(segs, t) {
  const list = segs || [];
  for (let i = 0; i < list.length; i++) {
    if (t < list[i].start) return i;   // in the gap before this one
    if (t < list[i].end) return i;     // inside this one
  }
  return Math.max(0, list.length - 1);
}

// Stable CONTENT keys for a segment list. Callers rebuild these arrays on every
// render, so effects must not key off array identity — see SegmentVideo.
//
// Bounds and rates are keyed separately on purpose: changing a boundary should
// restart the panel, but nudging a speed magnitude should only change the
// playback rate, not yank playback back to the start.
export function segmentsKey(segs) {
  return (segs || []).map((s) => `${s.start.toFixed(3)}-${s.end.toFixed(3)}`).join(",");
}

export function ratesKey(segs) {
  return (segs || []).map((s) => String(s.speed ?? 1)).join(",");
}
