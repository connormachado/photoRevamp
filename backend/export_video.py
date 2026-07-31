"""
Video export → Apple Photos
===========================
Renders the approved keep-segments of a source video into one Photos-friendly
clip, imports it into Apple Photos dated to match the original, and reveals it.

The source is opened read-only and never deleted or modified — the exported clip
is a NEW asset that lands beside the original in the Photos timeline. Deleting
the original stays a manual decision.

Plain functions only (no Flask); server.py wraps these in a route, matching the
convention used by cleanup.py / stats.py.

Why date AND location are stamped twice
----------------------------------------
Both the date and the GPS location get two independent write attempts:

1. Container tags (`creation_time` + the ISO-6709 location string), written by
   `render_segments`/`render_plan` as part of the same ffmpeg pass that does
   the trimming.
2. `set date of media item id ...` / `set location of media item id ...`
   afterwards in `import_to_photos`.

(2) was expected to fail as read-only but is in fact accepted on current macOS
for both properties (verified against real imported items via `osascript`).
**(1) alone is NOT sufficient** — verified for both: an item imported with only
the container tags present came back from Photos with the wrong date (see
`_try_set_item_date`'s day/month-ordering bug, fixed after being caught this
way) and `location: missing value`. (2) is what actually lands the correct
value; (1) is kept anyway since it needs no automation permission and costs
nothing extra. `import_to_photos` reports which of (2) took via `date_set` /
`location_set`.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg

from edit_boundaries import Piece
from utils import DEFAULT_DB_PATH

# Same pip-bundled ffmpeg the rest of the Climb Cutter uses — there is no system
# ffmpeg (or ffprobe) on PATH, so metadata is parsed from `ffmpeg -i` stderr.
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

MOTION_DIR = DEFAULT_DB_PATH / "motion_review"
EXPORTS_DIR = MOTION_DIR / "exports"


# ── Source metadata ───────────────────────────────────────────────────────────

def read_source_metadata(path: Path | str) -> dict:
    """Return {date, date_utc, gps, rotation} scraped from the container tags.

    `date` prefers com.apple.quicktime.creationdate (local wall-clock time with a
    UTC offset, e.g. 2026-02-11T21:31:00-0500) because that is the timestamp
    Photos files by. It falls back to the plain `creation_time` tag, which is
    UTC. Either is accepted by ffmpeg's -metadata creation_time.

    `gps` is the raw ISO-6709 string (e.g. "+43.6552-072.2412+180.799/") ready to
    be written straight back out; None when the source carries no location.
    """
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    stderr = result.stderr  # ffmpeg exits non-zero with no output file; expected.

    info: dict = {"date": None, "date_utc": None, "gps": None, "rotation": None}

    m = re.search(r"com\.apple\.quicktime\.creationdate\s*:\s*(\S+)", stderr)
    if m:
        info["date"] = m.group(1)

    m = re.search(r"creation_time\s*:\s*(\S+)", stderr)
    if m:
        info["date_utc"] = m.group(1)

    if not info["date"]:
        info["date"] = info["date_utc"]

    # Apple writes the ISO-6709 tag; ffmpeg's own muxer writes plain `location`.
    # Match either so this doubles as a verification read on our own exports.
    m = (re.search(r"com\.apple\.quicktime\.location\.ISO6709\s*:\s*(\S+)", stderr)
         or re.search(r"^\s*location\s*:\s*(\S+)", stderr, re.MULTILINE))
    if m:
        info["gps"] = m.group(1)

    m = re.search(r"displaymatrix:\s*rotation of ([-\d.]+) degrees", stderr)
    if m:
        info["rotation"] = float(m.group(1))

    return info


# ── Render ────────────────────────────────────────────────────────────────────

def render_segments(
    source_path: Path | str,
    kept_segments: list,
    out_name: str | None = None,
    metadata: dict | None = None,
) -> Path:
    """Concatenate *kept_segments* of *source_path* into one Photos-friendly file.

    kept_segments is a list of {"start": s, "end": e} dicts (or (s, e) tuples) in
    seconds — the same shape motion_review stores. Uses the concat demuxer's
    inpoint/outpoint directives so no intermediate segment files are written,
    mirroring video_motion.make_trimmed_clip.

    Unlike make_trimmed_clip (which is -c copy to MKV, keyframe-snapped, and
    reflects the *proposed* cuts) this RE-ENCODES, so boundaries are frame-exact
    and the container is one Photos will accept.

    Output: H.264 / AAC in an MP4 at -crf 18 (visually transparent for phone
    footage). To emit HEVC in a .mov instead — smaller files, matches what the
    iPhone shot originally, but slower to encode — swap libx264 for hevc_videotoolbox
    with -tag:v hvc1 and give out_path a .mov suffix.

    *metadata* is the dict from read_source_metadata; its date and GPS are baked
    into the output here so Photos picks them up at import time.

    Thin wrapper over render_plan: plain keep-segments become plain Pieces, which
    take the concat-demuxer path below — the exact command this function has
    always run.
    """
    pieces = [Piece(s, e) for s, e in _normalize_segments(kept_segments)]
    return render_plan(source_path, pieces, out_name, metadata)


def render_plan(
    source_path: Path | str,
    plan: list,
    out_name: str | None = None,
    metadata: dict | None = None,
) -> Path:
    """Render an edit *plan* (a list of edit_boundaries.Piece) into one MP4.

    The plan comes from `edit_boundaries.build_plan`, which asked each region's
    type what it does to the video. This function never asks what a region *is* —
    it only executes pieces, so new boundary types need no changes here.

    Two paths, picked automatically:

    * **plain** — every piece is a straight copy (speed 1, no filters). Uses the
      concat demuxer's inpoint/outpoint directives, no intermediate files. This
      is the drop-only path the Climb Cutter has always used, byte for byte.
    * **filtered** — some piece needs a transform. Falls back to a single input
      plus a filter_complex graph (trim/setpts per piece, concat at the end),
      since the concat demuxer cannot vary playback rate per entry. This path
      needs a second, stream-copy pass to strip a leftover display matrix — see
      `_strip_display_matrix`.
    """
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"source video missing: {src}")

    pieces = _normalize_plan(plan)
    if not pieces:
        raise ValueError("no keep segments to render — nothing would be exported")

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EXPORTS_DIR / (out_name or f"{src.stem}_trimmed.mp4")

    meta = metadata or {}
    tmp_dir = Path(tempfile.mkdtemp(prefix="vx_render_"))
    try:
        plain = all(p.is_plain for p in pieces)
        if plain:
            cmd = _concat_demuxer_cmd(src, pieces, tmp_dir)
            # The concat path writes the finished file directly, metadata and
            # all — this command must stay byte-for-byte what it has always been.
            encode_target = out_path
        else:
            cmd = _filter_graph_cmd(src, pieces)
            # The filtered path encodes to a temp file first; the metadata is
            # stamped by the stream-copy pass below, because a `-c copy` remux
            # drops creation_time (verified) and would throw it away.
            encode_target = tmp_dir / "filtered.mp4"

        # ── shared encoder tail ───────────────────────────────────────────────
        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ]
        # Rotation needs no flag: on RE-ENCODE ffmpeg autorotates, baking the
        # source's display matrix into the pixels (verified — a 1920x1080 source
        # carrying a -90° matrix comes out as a true 1080x1920 portrait file).
        # Do NOT add -metadata:s:v:0 rotate= here; it is a no-op in ffmpeg 7
        # (re-verified), and if it ever started working it would rotate footage
        # that is already upright.
        if plain:
            cmd += _metadata_args(meta)
        cmd.append(str(encode_target))

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not encode_target.exists() or encode_target.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg render failed: {(proc.stderr or '')[-600:]}")

        if not plain:
            _strip_display_matrix(encode_target, out_path, meta)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return out_path


def _metadata_args(meta: dict) -> list:
    """The date + GPS tags Photos reads at import."""
    args = []
    if meta.get("date"):
        args += ["-metadata", f"creation_time={meta['date']}"]
    if meta.get("gps"):
        args += [
            "-metadata", f"com.apple.quicktime.location.ISO6709={meta['gps']}",
            "-metadata", f"location={meta['gps']}",
        ]
    return args


def _strip_display_matrix(rendered: Path, out_path: Path, meta: dict) -> None:
    """Stream-copy *rendered* to *out_path*, dropping any display matrix.

    THE FILTERED PATH ROTATES TWICE WITHOUT THIS. ffmpeg autorotates the
    filtergraph's input, so the pixels come out of a -90° iPhone source as a true
    1080x1920 portrait — correct — but on this path it *also* copies the source's
    display matrix onto the output stream, so a player rotates the already-upright
    pixels a second time and the clip lands sideways in Photos. The concat-demuxer
    path does not do this, which is why drop-only exports were always fine.

    `-display_rotation 0` on the INPUT is the only thing that clears it; it
    overrides the matrix to zero so nothing is written out. `-metadata:s:v:0
    rotate=0`, `-map_metadata:s:v:0 -1` and `-noautorotate` were all tried and all
    leave the matrix in place. Because this only deletes a now-redundant tag —
    ffmpeg already did the pixel work — it is correct for any source rotation,
    not just the -90° case there is footage for.

    Re-stamping the metadata here is not optional: a `-c copy` remux drops
    creation_time (GPS happens to survive). Verified both ways.
    """
    cmd = [
        FFMPEG, "-y",
        "-display_rotation", "0",
        "-i", str(rendered),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c", "copy",
    ] + _metadata_args(meta) + ["-movflags", "+faststart", str(out_path)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg de-rotate remux failed: {(proc.stderr or '')[-600:]}")


def _concat_demuxer_cmd(src: Path, pieces: list, tmp_dir: Path) -> list:
    """Input args for the plain path: one ffconcat entry per piece."""
    list_txt = tmp_dir / "list.txt"
    with open(list_txt, "w") as lf:
        lf.write("ffconcat version 1.0\n")
        for p in pieces:
            lf.write(f"file '{src.resolve()}'\n")
            lf.write(f"inpoint {p.start:.6f}\n")
            lf.write(f"outpoint {p.end:.6f}\n")

    return [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_txt),
        # iPhone .MOV carries extra streams this build can't decode (4-channel
        # `apac` spatial audio, several mebx data tracks). Map ONLY the first
        # video + first audio stream; letting ffmpeg auto-map fails outright.
        "-map", "0:v:0", "-map", "0:a:0?",
    ]


def _filter_graph_cmd(src: Path, pieces: list) -> list:
    """Input args for the filtered path: trim/setpts per piece, then concat.

    Audio is trimmed and time-stretched in lockstep with video or it desyncs.
    `-map 0:a:0?`'s "optional audio" has no filtergraph equivalent, so a source
    with no audio track gets a video-only graph instead.
    """
    with_audio = has_audio_stream(src)

    chains, labels = [], []
    for i, p in enumerate(pieces):
        vf = [f"trim=start={p.start:.6f}:end={p.end:.6f}", _setpts(p.speed)]
        vf += list(p.vf)
        chains.append(f"[0:v]{','.join(vf)}[v{i}]")
        labels.append(f"[v{i}]")

        if with_audio:
            af = [f"atrim=start={p.start:.6f}:end={p.end:.6f}", "asetpts=PTS-STARTPTS"]
            af += _atempo_chain(p.speed)
            af += list(p.af)
            chains.append(f"[0:a]{','.join(af)}[a{i}]")
            labels.append(f"[a{i}]")

    n = len(pieces)
    if with_audio:
        chains.append(f"{''.join(labels)}concat=n={n}:v=1:a=1[vout][aout]")
        maps = ["-map", "[vout]", "-map", "[aout]"]
    else:
        chains.append(f"{''.join(labels)}concat=n={n}:v=1:a=0[vout]")
        maps = ["-map", "[vout]"]

    return [FFMPEG, "-y", "-i", str(src.resolve()),
            "-filter_complex", ";".join(chains)] + maps


def _setpts(speed: float) -> str:
    if abs(speed - 1.0) < 1e-9:
        return "setpts=PTS-STARTPTS"
    return f"setpts=(PTS-STARTPTS)/{speed:.6f}"


def _atempo_chain(speed: float) -> list:
    """atempo clamps to 0.5–2.0 per instance, so fast/slow ramps get chained."""
    if abs(speed - 1.0) < 1e-9:
        return []
    remaining, out = float(speed), []
    while remaining > 2.0 + 1e-9:
        out.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        out.append("atempo=0.5")
        remaining /= 0.5
    out.append(f"atempo={remaining:.6f}")
    return out


def has_audio_stream(path: Path | str) -> bool:
    """True when the source carries a decodable audio stream."""
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    return bool(re.search(r"Stream #0:\d+.*: Audio:", result.stderr or ""))


def _normalize_plan(plan: list) -> list:
    """Accept Pieces (or bare segments) and drop empty spans, preserving order."""
    out = []
    for item in plan or []:
        if isinstance(item, Piece):
            if item.source_duration > 1e-3:
                out.append(item)
            continue
        try:
            if isinstance(item, dict):
                s, e = float(item["start"]), float(item["end"])
            else:
                s, e = float(item[0]), float(item[1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if e - s > 1e-3:
            out.append(Piece(max(0.0, s), e))
    return out


def _normalize_segments(segments: list) -> list:
    """Accept dicts or tuples, drop empties, return sorted (start, end) floats."""
    out = []
    for seg in segments or []:
        try:
            if isinstance(seg, dict):
                s, e = float(seg["start"]), float(seg["end"])
            else:
                s, e = float(seg[0]), float(seg[1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if e - s > 1e-3:
            out.append((max(0.0, s), e))
    out.sort()
    return out


# ── Photos import ─────────────────────────────────────────────────────────────

def import_to_photos(
    video_path: Path | str,
    original_date: str | None = None,
    gps: str | None = None,
) -> dict:
    """Import *video_path* into Apple Photos and return {success, item_id, ...}.

    The date and GPS are expected to already be baked into the file by
    render_segments (see the module docstring); `original_date`/`gps` are
    accepted so the AppleScript attempts below have something to set. Neither
    container tag is reliably picked up by Photos' importer on its own
    (verified for both) — the AppleScript `date`/`location` properties are
    what actually land the value; the container tags are kept as the
    lower-effort, no-permission-needed mechanism that should agree with them.

    Returns {"success": bool, "item_id": str|None, "date_set": bool,
    "location_set": bool, "error"?: str} — the caller decides whether a
    failed date/location-set is fatal (it is not; a failure just means the
    clip landed without that field being force-corrected).
    """
    path = Path(video_path).resolve()
    if not path.exists():
        return {"success": False, "item_id": None, "date_set": False,
                "error": f"rendered file missing: {path}"}

    # Strip quotes/backslashes so the path can't break out of the string literal,
    # same defensive move as cleanup.reveal_in_photos.
    safe_path = str(path).replace('"', "").replace("\\", "")

    script = (
        f'tell application "Photos"\n'
        f'  set newItems to import {{POSIX file "{safe_path}"}} skip check duplicates yes\n'
        f'  if newItems is missing value then return ""\n'
        f'  if (count of newItems) is 0 then return ""\n'
        f'  return id of item 1 of newItems\n'
        f'end tell'
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        return {"success": False, "item_id": None, "date_set": False,
                "error": (e.stderr or "").strip() or str(e)}

    item_id = (proc.stdout or "").strip()
    if not item_id:
        return {"success": False, "item_id": None, "date_set": False,
                "error": "Photos returned no imported item (already in library?)"}

    # Second date mechanism: the AppleScript `date` property. Verified settable
    # on current macOS. A failure here is reported rather than raised — a failed
    # date-set is not treated as a failed export.
    date_set = False
    if original_date:
        date_set = _try_set_item_date(item_id, original_date)

    # Same story for location: the ISO-6709 tag baked into the render isn't
    # reliably read by Photos' importer either (verified — an item imported
    # with the tag present still came back with `location: missing value`), so
    # force it the same way via AppleScript's settable `location` property.
    location_set = False
    if gps:
        location_set = _try_set_item_location(item_id, gps)

    return {"success": True, "item_id": item_id, "date_set": date_set,
            "location_set": location_set}


def _try_set_item_date(item_id: str, iso_date: str) -> bool:
    """Attempt `set date of media item ... to ...`. True if Photos accepted it."""
    # AppleScript wants a date object, not an ISO string. Build one from parts so
    # we don't depend on the machine's date-string locale.
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})", iso_date)
    if not m:
        return False
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    safe_id = item_id.replace('"', "").replace("\\", "")
    script = (
        f'set theDate to current date\n'
        # `day` is set to 1 before `year`/`month` so the intermediate date is
        # always valid — setting month while `day` still holds *today's*
        # day-of-month overflows into the following month whenever today's
        # day exceeds the target month's length (e.g. running this on the
        # 31st against a February date rolls over to March 3rd, then `set day
        # to 11` lands on March 11th instead of February 11th). Reproduced
        # directly via osascript; day-of-month bugs like this only show up on
        # the days it can occur, which is why this passed testing before.
        f'set day of theDate to 1\n'
        f'set year of theDate to {y}\n'
        f'set month of theDate to {mo}\n'
        f'set day of theDate to {d}\n'
        f'set time of theDate to {h * 3600 + mi * 60 + s}\n'
        f'tell application "Photos"\n'
        f'  set date of media item id "{safe_id}" to theDate\n'
        f'end tell'
    )
    try:
        subprocess.run(["osascript", "-e", script],
                       check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _parse_iso6709(gps: str) -> tuple[float, float] | None:
    """Parse an ISO-6709 string ("+43.6552-072.2412+180.799/") to (lat, lon).

    Altitude (the third signed number, if present) is dropped — Photos'
    AppleScript `location` property only takes {latitude, longitude}.
    """
    m = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?/?$",
                 (gps or "").strip())
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def _try_set_item_location(item_id: str, gps: str) -> bool:
    """Attempt `set location of media item ... to {lat, lon}`.

    Mirrors _try_set_item_date: the ISO-6709 tag baked into the render at
    encode time isn't reliably read by Photos' importer, so this forces it via
    AppleScript's settable `location` property after import.
    """
    parsed = _parse_iso6709(gps)
    if not parsed:
        return False
    lat, lon = parsed
    safe_id = item_id.replace('"', "").replace("\\", "")
    script = (
        f'tell application "Photos"\n'
        f'  set location of media item id "{safe_id}" to {{{lat}, {lon}}}\n'
        f'end tell'
    )
    try:
        subprocess.run(["osascript", "-e", script],
                       check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError:
        return False


def reveal_in_photos(item_id: str) -> dict:
    """Activate Photos and spotlight the imported item. {"success", "error"?}.

    Same two-step activate-then-spotlight as cleanup.reveal_in_photos, against an
    item id Photos handed us directly at import rather than a stored apple_uuid.
    """
    safe_id = (item_id or "").replace('"', "").replace("\\", "")
    if not safe_id:
        return {"success": False, "error": "no item id to reveal"}
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Photos" to activate'],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["osascript", "-e",
             f'tell application "Photos" to spotlight media item id "{safe_id}"'],
            check=True, capture_output=True, text=True,
        )
        return {"success": True}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": (e.stderr or "").strip() or str(e)}


# ── Convenience: the whole pipeline ───────────────────────────────────────────

def export_and_import(
    source_path: Path | str,
    kept_segments: list,
    out_name: str | None = None,
) -> dict:
    """render_plan → import_to_photos → reveal_in_photos, in that order.

    *kept_segments* is an edit plan (a list of edit_boundaries.Piece) or, for
    callers that only drop footage, a plain [{start, end}] keep list — both go
    through render_plan, which picks its render path from the pieces themselves.

    Returns {rendered_path, size_bytes, source_date, gps, imported, revealed}.
    Raises on render failure (nothing was written to Photos); import/reveal
    problems come back inside the dict so the caller can report them without the
    export being treated as a crash.
    """
    meta = read_source_metadata(source_path)
    rendered = render_plan(source_path, kept_segments, out_name, meta)

    imported = import_to_photos(rendered, meta.get("date"), meta.get("gps"))
    revealed = (reveal_in_photos(imported["item_id"])
                if imported.get("success") else {"success": False,
                                                 "error": "import failed; nothing to reveal"})

    return {
        "rendered_path": str(rendered),
        "size_bytes": rendered.stat().st_size,
        "source_date": meta.get("date"),
        "gps": meta.get("gps"),
        "imported": imported,
        "revealed": revealed,
    }
