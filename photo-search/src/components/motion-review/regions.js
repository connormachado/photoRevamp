// Pure region math shared across the review room (mirrors edit_boundaries.py so
// the live preview matches what gets persisted).

import { getType } from "./boundaryTypes";

let counter = 0;
function newId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `r-${crypto.randomUUID().slice(0, 8)}`;
  }
  counter += 1;
  return `r-${Date.now().toString(36)}${counter}`;
}

export function makeRegion(typeId, start, end) {
  const type = getType(typeId);
  return {
    id: newId(),
    type: type.id,
    start,
    end,
    params: { ...type.defaultParams },
  };
}

// Upgrade a legacy [{start, end}] cut list into cut regions.
export function regionsFromCuts(cuts) {
  return (cuts || []).map((c) => makeRegion("cut", c.start, c.end));
}

// Derived cut_segments: the spans of every footage-removing region. This is what
// the preview panels and the savings estimate still consume.
export function regionsToCuts(regions) {
  return (regions || [])
    .filter((r) => getType(r.type).removesFootage)
    .map((r) => ({ start: r.start, end: r.end }))
    .sort((a, b) => a.start - b.start);
}

export function sortRegions(regions) {
  return [...(regions || [])].sort((a, b) => a.start - b.start);
}

// The ordered list of PIECES the finished video is made of — the display-side
// mirror of edit_boundaries.build_plan.
//
// Walks [0, duration]: every gap between regions is untouched footage and emits
// a plain piece; every region hands off to its type's `toPieces` hook. With
// cut-only regions this returns exactly what complementSegments returns, which
// is why the drop-only preview is unchanged.
//
// This is what the Trimmed panel plays, so the preview shows what the export
// will actually produce instead of only knowing how to skip cuts.
export function buildPlan(regions, duration) {
  const regs = sortRegions(regions);
  const plan = [];
  let cursor = 0;
  for (const r of regs) {
    const s = Math.max(0, r.start);
    const e = Math.min(duration, r.end);
    if (s > cursor + 1e-3) plan.push({ start: cursor, end: s, speed: 1 });
    for (const p of getType(r.type).toPieces(r) || []) {
      plan.push({ speed: 1, ...p });
    }
    cursor = Math.max(cursor, e);
  }
  if (cursor < duration - 1e-3) plan.push({ start: cursor, end: duration, speed: 1 });
  return plan;
}

// Length of the finished video. Derived from the plan rather than computed
// separately: the header used to do its own covered/transformed arithmetic while
// the preview panel did its own thing, which is exactly how the two came to
// disagree about speed regions. One source of truth.
export function outputDuration(regions, duration) {
  const total = buildPlan(regions, duration).reduce(
    (acc, p) => acc + (p.end - p.start) / (p.speed || 1), 0);
  return Math.max(0, total);
}

// Two region lists equal within a small epsilon, ignoring ids (which are UI-side
// handles). Used for the "edited" badge.
export function regionsEqual(a, b) {
  a = sortRegions(a);
  b = sortRegions(b);
  if (a.length !== b.length) return false;
  return a.every((r, i) =>
    r.type === b[i].type &&
    Math.abs(r.start - b[i].start) < 1e-3 &&
    Math.abs(r.end - b[i].end) < 1e-3 &&
    JSON.stringify(r.params || {}) === JSON.stringify(b[i].params || {})
  );
}

// Insert a region of *typeId* at *t*, sized to fit the free gap around it.
// Returns the unchanged list when there is no room (the caller selects instead).
export function addRegionAt(regions, typeId, t, duration, fps = 30) {
  const sorted = sortRegions(regions);
  if (sorted.some((r) => t >= r.start - 1e-6 && t <= r.end + 1e-6)) return null;

  const type = getType(typeId);
  const minW = (type.minWidthFrames || 2) / (fps || 30);
  const prevEnd = sorted.filter((r) => r.end <= t).reduce((acc, r) => Math.max(acc, r.end), 0);
  const nextStart = sorted.find((r) => r.start >= t)?.start ?? duration;
  if (nextStart - prevEnd < minW) return null;

  const want = type.defaultLengthSeconds || 1.5;
  let start = t;
  let end = Math.min(t + want, nextStart);
  if (end - start < minW) {                 // ran into the next region — grow left
    start = Math.max(prevEnd, end - want);
  }
  if (end - start < minW) return null;

  return sortRegions([...(regions || []), { ...makeRegion(typeId, start, end) }]);
}

export function removeRegion(regions, id) {
  return (regions || []).filter((r) => r.id !== id);
}

// Type-agnostic by construction: the region list is flat and mixes every
// type together, so dropping everything never has to name a type — it
// already covers whatever types exist today or get registered later. When
// an undo stack exists, swap the call site for an undoable command built
// from the previous list and drop the confirm dialog around it.
export function clearAllRegions() {
  return [];
}
