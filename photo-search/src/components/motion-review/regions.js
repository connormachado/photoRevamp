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

// Length of the finished video: untouched footage plus whatever each region's
// type says its span contributes.
export function outputDuration(regions, duration) {
  const covered = (regions || []).reduce((acc, r) => acc + (r.end - r.start), 0);
  const transformed = (regions || []).reduce(
    (acc, r) => acc + getType(r.type).outputDuration(r), 0);
  return Math.max(0, duration - covered + transformed);
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
