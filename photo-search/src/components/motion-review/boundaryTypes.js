// Edit-boundary registry — display half.
//
// ONE declarative place where each kind of timeline edit is defined for the UI:
// id, label, icon, colour, default params, and how it renders on the timeline.
// Adding a new edit type later = add one entry here (plus its export hook in the
// backend registry) and nothing else — CutTimeline, the toolbar and ReviewStage
// all read from this map and never branch on a specific type.
//
// Mirror file
// -----------
// The export half lives in `backend/edit_boundaries.py` — default params and the
// apply-on-export hook that decides what ffmpeg actually does with the span. The
// two files are keyed by the same TYPE ID STRINGS; that id is the contract
// between them. Keep `defaultParams` in sync across both.
//
// The data model
// --------------
// A *region* is the first-class entity, in seconds:
//
//     { id: "r-8f2a", type: "cut", start: 5.25, end: 9.0, params: {} }
//
// Regions are sorted by start and never overlap. Time NOT covered by a region is
// untouched footage. See ./regions.js for the helpers.

const fmt = (s) => {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
};

export const BOUNDARY_TYPES = {
  cut: {
    id: "cut",
    label: "Cut",
    icon: "✂",
    color: "#f87171",                    // drag handles, footer text, chip accent
    fill: "rgba(248,113,113,0.35)",      // the block on the timeline
    glyphColor: "#fecaca",
    defaultParams: {},
    minWidthFrames: 2,                   // a region can't get thinner than this
    defaultLengthSeconds: 1.5,           // width of a freshly added region
    glyph: () => "✂",
    describe: (r) => `cut ${fmt(r.start)} → ${fmt(r.end)}`,
    // Seconds this region contributes to the exported video — mirrors the
    // backend hook (cut drops its span, so nothing).
    outputDuration: () => 0,
    removesFootage: true,
    // Optional `renderBlock(region, ctx)` escape hatch: omitted here, so cut uses
    // CutTimeline's default block driven by fill/glyph/glyphColor. Types that
    // need custom chrome (a speed label, a gradient) override it.
  },
  // Next entry goes here (Prompt 2: "speed"). Nothing else needs to change.
};

export const TYPE_LIST = Object.values(BOUNDARY_TYPES);
export const DEFAULT_TYPE_ID = "cut";

// Unknown ids fall back to the default so a stale/foreign region still renders
// instead of blowing up the timeline.
export function getType(id) {
  return BOUNDARY_TYPES[id] || BOUNDARY_TYPES[DEFAULT_TYPE_ID];
}
