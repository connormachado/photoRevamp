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
