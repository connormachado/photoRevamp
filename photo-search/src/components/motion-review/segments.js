// Pure segment math shared across the review room (mirrors the backend so the
// live preview matches what gets persisted).

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

// Two segment lists equal within a small epsilon (used for the "edited" badge).
export function segmentsEqual(a, b) {
  a = a || [];
  b = b || [];
  if (a.length !== b.length) return false;
  return a.every((s, i) => Math.abs(s.start - b[i].start) < 1e-3 && Math.abs(s.end - b[i].end) < 1e-3);
}
