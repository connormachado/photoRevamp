"""
Edit-boundary registry (Climb Cutter)
=====================================
ONE declarative place where each kind of timeline edit is defined for the export
side: its id, its default params, whether it removes footage, what it contributes
to the output duration, and — the important bit — its **apply-on-export hook**.

Adding a new edit type later = add one `BoundaryType` entry here (plus its
display half in the frontend registry) and nothing else. Neither
`motion_review.py` nor `export_video.py` knows what a "cut" is; they iterate
regions and call the hooks.

Mirror file
-----------
The display half of the registry lives in
`photo-search/src/components/motion-review/boundaryTypes.js` — colour, icon,
label, how the region renders on the timeline. The two files are keyed by the
same **type id strings**; that id is the contract between them. Keep
`default_params` in sync across both.

The data model
--------------
A *region* is the first-class entity, in seconds, on the wire and on disk::

    {"id": "r-8f2a", "type": "cut", "start": 5.25, "end": 9.0, "params": {}}

Regions are sorted by start and never overlap. Time NOT covered by a region is
an implicit keep at speed 1. `build_plan` walks [0, duration] and turns regions
+ gaps into an ordered list of `Piece`s — the render plan that `export_video`
executes. With only "cut" registered, that plan is exactly the old keep list.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable


# ── The plan ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Piece:
    """One span of source that survives into the output, post-transform.

    speed == 1.0 with no vf/af is a "plain" piece — a straight copy of that span,
    which lets `export_video.render_plan` use the fast concat-demuxer path. Any
    piece that needs a filter forces the whole render onto the filter_complex
    path, so hooks should leave plain spans plain.
    """
    start: float
    end: float
    speed: float = 1.0
    vf: tuple = ()   # extra per-piece video filters, applied after setpts
    af: tuple = ()   # extra per-piece audio filters, applied after asetpts

    @property
    def source_duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def output_duration(self) -> float:
        return self.source_duration / (self.speed or 1.0)

    @property
    def is_plain(self) -> bool:
        return abs(self.speed - 1.0) < 1e-9 and not self.vf and not self.af


# ── The registry ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BoundaryType:
    """One registered kind of edit boundary.

    apply_on_export(region, ctx) -> list[Piece]
        What this region does to the video during render. Return [] to drop the
        span, one Piece to transform it, several to expand it. `ctx` carries
        {"duration", "source_path", "regions"} for hooks that need context.
    output_duration(region) -> float
        Seconds this region contributes to the finished video. Must agree with
        what apply_on_export emits; it exists so the UI/savings math can be
        computed without building a plan.
    """
    id: str
    label: str
    default_params: dict
    removes_footage: bool
    apply_on_export: Callable[[dict, dict], list]
    output_duration: Callable[[dict], float]
    merge_adjacent: bool = True   # overlapping same-type neighbours collapse into one


def _cut_apply(region: dict, ctx: dict) -> list:
    """Cut: the span is dropped, so it emits no pieces at all."""
    return []


# ── speed ─────────────────────────────────────────────────────────────────────
# params: {"direction": "up"|"down", "magnitude": float}
# Direction comes ONLY from the toggle; magnitude is always an unsigned number.
#   up   N  →  speed = N     → setpts=PTS/N   (N× faster)
#   down N  →  speed = 1/N   → setpts=PTS*N   (N× slower)
# Magnitude 1.0 is a deliberate no-op in either direction, which is why the UI
# clamps its step-down there instead of walking below 1.

SPEED_MIN_MAGNITUDE = 1.0
SPEED_MAX_MAGNITUDE = 20.0


def _effective_speed(params: dict) -> float:
    """The playback-rate multiplier a speed region's params ask for."""
    try:
        mag = float((params or {}).get("magnitude", 2.0))
    except (TypeError, ValueError):
        mag = 2.0
    mag = max(SPEED_MIN_MAGNITUDE, min(SPEED_MAX_MAGNITUDE, mag))
    return mag if (params or {}).get("direction", "up") != "down" else 1.0 / mag


def _speed_apply(region: dict, ctx: dict) -> list:
    """Speed: the span survives whole, retimed by one Piece.

    At magnitude 1 this returns a PLAIN piece on purpose — a speed region parked
    at 1× must not drag the whole render off the fast concat-demuxer path.

    The `fps` pin is not cosmetic. setpts rescales timestamps, so a 2× piece
    lands at twice the source frame rate and a slowed piece at half; forcing the
    source fps afterwards decimates/duplicates back to a uniform rate so the
    concat filter is handed consistent streams.
    """
    start, end = float(region["start"]), float(region["end"])
    speed = _effective_speed(region.get("params") or {})
    if abs(speed - 1.0) < 1e-9:
        return [Piece(start, end)]
    fps = ctx.get("fps")
    vf = (f"fps={float(fps):.6g}",) if fps else ()
    return [Piece(start, end, speed=speed, vf=vf)]


def _speed_output_duration(region: dict) -> float:
    span = max(0.0, float(region["end"]) - float(region["start"]))
    return span / _effective_speed(region.get("params") or {})


REGISTRY: dict[str, BoundaryType] = {
    "cut": BoundaryType(
        id="cut",
        label="Cut",
        default_params={},
        removes_footage=True,
        apply_on_export=_cut_apply,
        output_duration=lambda region: 0.0,
    ),
    "speed": BoundaryType(
        id="speed",
        label="Speed",
        default_params={"direction": "up", "magnitude": 2.0},
        removes_footage=False,
        apply_on_export=_speed_apply,
        output_duration=_speed_output_duration,
    ),
    # Next entry goes here. Nothing else needs to change.
}

DEFAULT_TYPE_ID = "cut"


def get_type(type_id: str) -> BoundaryType | None:
    return REGISTRY.get(type_id)


def type_ids() -> list[str]:
    return list(REGISTRY.keys())


# ── Region hygiene ────────────────────────────────────────────────────────────

def _new_region_id() -> str:
    return f"r-{uuid.uuid4().hex[:8]}"


def sanitize_regions(regions: list, duration: float) -> list[dict]:
    """Clamp to [0, duration], drop empties/unknown types, sort, merge overlaps.

    The frontend already constrains drags, but the backend is authoritative — a
    malformed POST can't produce garbage regions. Overlapping neighbours of the
    SAME type merge (matching the old _sanitize_cuts behaviour); overlapping
    neighbours of DIFFERENT types are truncated so the later one starts where the
    earlier one ends, because a span can only have one transform.
    """
    # A POST can carry anything at all under "regions" — a number, a string, an
    # object. This function is the authoritative gate, so a non-list is treated
    # as "no regions" rather than being iterated (a bare int raised TypeError
    # straight out of the route and 500'd).
    if not isinstance(regions, (list, tuple)):
        regions = []

    cleaned = []
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        type_id = reg.get("type") or DEFAULT_TYPE_ID
        btype = get_type(type_id)
        if btype is None:
            continue
        try:
            s = max(0.0, float(reg["start"]))
            e = min(float(duration), float(reg["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if e - s <= 1e-3:
            continue
        params = dict(btype.default_params)
        if isinstance(reg.get("params"), dict):
            params.update(reg["params"])
        cleaned.append({
            "id": str(reg.get("id") or _new_region_id()),
            "type": type_id,
            "start": s,
            "end": e,
            "params": params,
        })

    cleaned.sort(key=lambda r: (r["start"], r["end"]))

    merged: list[dict] = []
    for reg in cleaned:
        if not merged:
            merged.append(reg)
            continue
        prev = merged[-1]
        if reg["start"] > prev["end"] - 1e-9:      # no overlap
            merged.append(reg)
        elif (reg["type"] == prev["type"]
              and get_type(reg["type"]).merge_adjacent
              and reg["params"] == prev["params"]):
            prev["end"] = max(prev["end"], reg["end"])
        else:                                       # different transforms — truncate
            reg["start"] = prev["end"]
            if reg["end"] - reg["start"] > 1e-3:
                merged.append(reg)

    for reg in merged:
        reg["start"] = round(reg["start"], 3)
        reg["end"] = round(reg["end"], 3)
    return merged


def regions_from_cuts(cuts: list) -> list[dict]:
    """Upgrade an old [{start, end}] cut list into cut regions.

    Used for reviews/proposals written before the registry existed, and for the
    legacy `cut_segments` field still accepted by the API.
    """
    if not isinstance(cuts, (list, tuple)):
        cuts = []
    out = []
    for seg in cuts:
        try:
            if isinstance(seg, dict):
                s, e = float(seg["start"]), float(seg["end"])
            else:
                s, e = float(seg[0]), float(seg[1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        out.append({
            "id": _new_region_id(),
            "type": "cut",
            "start": s,
            "end": e,
            "params": {},
        })
    return out


def regions_to_cuts(regions: list) -> list[dict]:
    """Derived `cut_segments`: the spans of every footage-removing region.

    Kept so the queue payload, the savings ledger and reviews/*.json keep their
    existing shape even though regions are now the source of truth.
    """
    cuts = []
    for reg in regions or []:
        btype = get_type(reg.get("type", ""))
        if btype is not None and btype.removes_footage:
            cuts.append({"start": round(float(reg["start"]), 3),
                         "end": round(float(reg["end"]), 3)})
    cuts.sort(key=lambda s: s["start"])
    return cuts


def regions_equal(a: list, b: list) -> bool:
    """Compare two region lists ignoring their ids (which are UI-side handles)."""
    def key(regs):
        return [(r.get("type"), round(float(r["start"]), 3), round(float(r["end"]), 3),
                 sorted((r.get("params") or {}).items()))
                for r in (regs or [])]
    return key(a) == key(b)


# ── The type-agnostic pipeline ────────────────────────────────────────────────

def build_plan(regions: list, duration: float, source_path: str = "",
               fps: float | None = None) -> list[Piece]:
    """Turn a region list into the ordered list of output Pieces.

    Walks [0, duration]: every gap between regions is untouched footage and emits
    a plain Piece; every region hands off to its type's apply_on_export hook.
    This is the whole extension point — the renderer never branches on type.

    *fps* is the source's frame rate (from `video_motion.probe`). It is optional
    because it only matters to hooks that retime footage — "speed" pins it so a
    setpts'd piece doesn't reach the concat filter at a multiplied frame rate.
    """
    regs = sorted(regions or [], key=lambda r: float(r["start"]))
    ctx = {"duration": float(duration), "source_path": source_path,
           "regions": regs, "fps": fps}

    plan: list[Piece] = []
    cursor = 0.0
    for reg in regs:
        s = max(0.0, float(reg["start"]))
        e = min(float(duration), float(reg["end"]))
        if s > cursor + 1e-3:
            plan.append(Piece(round(cursor, 3), round(s, 3)))
        btype = get_type(reg.get("type", ""))
        if btype is not None:
            plan.extend(btype.apply_on_export(reg, ctx) or [])
        cursor = max(cursor, e)
    if cursor < float(duration) - 1e-3:
        plan.append(Piece(round(cursor, 3), round(float(duration), 3)))
    return plan


def plan_output_duration(plan: list) -> float:
    """Length of the finished video, in seconds."""
    return round(sum(p.output_duration for p in plan or []), 3)


def plan_to_segments(plan: list) -> list[dict]:
    """The plan's source spans as [{start, end}] — the derived `keep_segments`."""
    return [{"start": round(p.start, 3), "end": round(p.end, 3)} for p in plan or []]
