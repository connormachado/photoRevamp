// The one time<->pixel mapping for the review-stage timeline. Nothing in
// CutTimeline (or a region type's renderBlock/renderOverlay) should compute a
// screen position any other way — that's what makes zoom a pure rescale
// instead of something that has to hunt down every place a position leaked in.
//
// Boundaries themselves are never touched by any of this: regions store
// {start, end} in seconds (see regions.js), and this module only maps those
// seconds to a screen pixel for the currently chosen zoom level. Zooming can
// never move a boundary's timestamp, only where it's drawn.

// Max zoom is defined as "the viewport never shows less than this many
// seconds of footage at once" rather than a pixel budget — zooming in past a
// couple of seconds stops helping you place a frame and just makes panning
// around tedious.
const MIN_VISIBLE_SECONDS = 3;

export function timeToPixel(t, pixelsPerSecond) {
  return t * pixelsPerSecond;
}

export function pixelToTime(x, pixelsPerSecond) {
  return pixelsPerSecond > 0 ? x / pixelsPerSecond : 0;
}

// The zoom level where the whole clip exactly fills the viewport — today's
// (pre-zoom) behavior, and the default/minimum zoom.
export function fitZoom(duration, viewportWidth) {
  if (!duration || duration <= 0 || !viewportWidth) return 1;
  return viewportWidth / duration;
}

export function maxZoom(viewportWidth) {
  if (!viewportWidth) return 1;
  return viewportWidth / MIN_VISIBLE_SECONDS;
}

// Clamps to [fit-to-viewport, max-useful-zoom]. Never lets you zoom OUT past
// seeing the whole clip — there's nothing productive on the other side of that.
export function clampZoom(pixelsPerSecond, duration, viewportWidth) {
  const min = fitZoom(duration, viewportWidth);
  const max = Math.max(min, maxZoom(viewportWidth));
  return Math.max(min, Math.min(max, pixelsPerSecond));
}

// "Nice" ruler intervals, in seconds — the candidates the ruler picks from so
// tick labels land on round numbers instead of e.g. every 7.3 seconds.
const NICE_INTERVALS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

// The smallest nice interval whose on-screen gap is still >= minPxGap, so
// ruler labels never crowd together as you zoom in or out — this is what
// makes the ruler re-densify automatically as pxPerSec changes.
export function niceTickInterval(pixelsPerSecond, minPxGap = 70) {
  for (const interval of NICE_INTERVALS) {
    if (interval * pixelsPerSecond >= minPxGap) return interval;
  }
  return NICE_INTERVALS[NICE_INTERVALS.length - 1];
}
