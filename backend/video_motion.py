"""
Video Motion / Dead-Time Detector
===================================
Detects "dead time" (static / no-motion) segments in a video via frame-to-frame
pixel diff, then produces:

  1. A lossless trimmed clip  — moving segments only, keyframe-snapped (-c copy).
  2. A timelapse preview      — the removed static sections, sped up 8x by default.

All outputs land under  photo_db/motion_review/   —  the Photos originals are
opened read-only and never modified.

Usage
-----
Bootstrap / inspect config:
    python video_motion.py --config-only

Probe a file (no system ffprobe needed):
    python video_motion.py --probe /path/to/clip.mov

Process one or more videos (path or Apple UUID):
    python video_motion.py --video /path/to/clip.mov
    python video_motion.py --video <uuid> --video /other/clip.mp4
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import imageio_ffmpeg

from utils import file_id, DEFAULT_DB_PATH

# ── Paths / constants ─────────────────────────────────────────────────────────

MOTION_DIR = DEFAULT_DB_PATH / "motion_review"
CONFIG_PATH = MOTION_DIR / "config.json"

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

DEFAULT_PHOTOS_LIBRARY = Path.home() / "Pictures" / "Photos Library.photoslibrary"
ORIGINALS_ROOT = DEFAULT_PHOTOS_LIBRARY / "originals"

# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_DEFAULTS = {
    "sample_fps": 4,
    "downscale_width": 320,
    "pixel_diff_threshold": 10.0,
    "window_seconds": 1.0,
    "min_static_seconds": 2.0,
    "pad_seconds": 0.75,
    "timelapse_speed": 8,
    "photos_originals_root": "",   # empty => use ORIGINALS_ROOT default
}


def load_config() -> dict:
    """Return config, writing defaults atomically if config.json is missing."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                on_disk = json.load(f)
            # Merge over defaults so any keys added in later versions are filled.
            return {**_CONFIG_DEFAULTS, **on_disk}
        except (json.JSONDecodeError, OSError):
            pass  # fall through to write defaults

    # Bootstrap: create dir and write defaults atomically.
    MOTION_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(CONFIG_PATH, _CONFIG_DEFAULTS)
    return dict(_CONFIG_DEFAULTS)


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write *data* as JSON to *path* atomically (temp + os.replace), mirroring
    the pattern in stats.py."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ── Video location ────────────────────────────────────────────────────────────

def resolve_video_path(video_arg: str, config: dict) -> Path:
    """Return the absolute Path for *video_arg*.

    If it is an existing file, use it directly.  Otherwise treat it as an Apple
    UUID and scan the first-character sub-folder inside the originals root for
    any recognised video extension, including the '_3' edited variant.
    """
    candidate = Path(video_arg)
    if candidate.exists():
        return candidate.resolve()

    # UUID-based lookup inside the Photos originals tree.
    root_str = config.get("photos_originals_root", "")
    originals_root = Path(root_str) if root_str else ORIGINALS_ROOT

    uuid = video_arg
    # Originals are stored flat: {root}/{UUID[0].upper()}/{UUID}.ext
    # (no extra UUID subdirectory — the library layout is one level deep).
    uuid_dir = originals_root / uuid[0].upper()
    exts = [".mov", ".mp4", ".MOV", ".MP4"]
    variants = [uuid_dir / f"{uuid}{ext}" for ext in exts]
    # Also check the '_3' edited variant (common for Live Photo-derived clips).
    variants += [uuid_dir / f"{uuid}_3.mov", uuid_dir / f"{uuid}_3.MOV"]

    for p in variants:
        if p.exists():
            return p.resolve()

    raise FileNotFoundError(
        f"Cannot find video for argument '{video_arg}'. "
        f"Tried as a file path and as UUID under {originals_root}."
    )


# ── Probe (ffmpeg-based, no ffprobe) ─────────────────────────────────────────

def probe(path: Path) -> dict:
    """Extract basic video metadata by parsing ffmpeg's stderr output.

    ffmpeg exits non-zero when given no output file — that is expected; we read
    stderr regardless of exit code.  Mirrors the subprocess.run style from
    cleanup.py.
    """
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    stderr = result.stderr

    info: dict = {
        "duration": None,
        "codec": None,
        "width": None,
        "height": None,
        "fps": None,
    }

    # Duration: "Duration: HH:MM:SS.ss"
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", stderr)
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info["duration"] = h * 3600 + mn * 60 + s

    # First video stream line, e.g.:
    #   Stream #0:0(und): Video: h264 (High), ..., 1920x1080, ..., 29.97 fps, ...
    vs = re.search(r"Stream #\S+: Video: (\w+).*?(\d{2,5})x(\d{2,5})", stderr)
    if vs:
        info["codec"] = vs.group(1)
        info["width"] = int(vs.group(2))
        info["height"] = int(vs.group(3))

    # FPS: prefer "29.97 fps" / "30 fps"; fall back to tbr.
    fps_m = re.search(r"([\d.]+)\s+fps", stderr)
    if not fps_m:
        fps_m = re.search(r"([\d.]+)\s+tbr", stderr)
    if fps_m:
        info["fps"] = float(fps_m.group(1))

    return info


# ── Frame sampling + diff ─────────────────────────────────────────────────────

def sampled_frame_diffs(path: Path, config: dict):
    """Decode downscaled grayscale frames via ffmpeg stdout and compute
    mean absolute frame-to-frame pixel differences.

    Returns (diffs, times) as float64 numpy arrays.  Both are empty arrays if
    the video yields fewer than two decoded frames.

    Resolution strategy
    -------------------
    We force a FIXED output size of W x H = downscale_width x H_FIXED, where
    H_FIXED = even(round(W * 9/16)).  Using `scale={W}:{H}` (both axes fixed)
    instead of `scale={W}:-2` (auto height) means ffmpeg will squash/stretch
    the frame to exactly W*H bytes, regardless of the coded dimensions,
    display-matrix rotation, or pixel-aspect-ratio of the source.  Portrait
    phone videos (coded 1920x1080 but displayed 1080x1920) would otherwise
    produce 320x568 frames while the code assumed 320x180, misaligning the
    reshape and producing garbage diffs.  Because we only measure frame-to-frame
    pixel diff magnitudes (not visual content), aspect distortion is harmless.
    """
    sample_fps      = config["sample_fps"]
    downscale_width = config["downscale_width"]

    W = downscale_width
    # Fixed height: 9/16 of width, rounded to nearest even integer.
    H = int(round(W * 9 / 16))
    if H % 2 != 0:
        H += 1

    cmd = [
        FFMPEG, "-i", str(path),
        "-vf", f"fps={sample_fps},scale={W}:{H},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    buf = result.stdout

    if not buf:
        return np.array([]), np.array([])

    n_pixels_per_frame = W * H
    if len(buf) % n_pixels_per_frame != 0:
        raise RuntimeError(
            f"sampled_frame_diffs: rawvideo buffer size {len(buf)} is not a "
            f"multiple of frame size {W}x{H}={n_pixels_per_frame}. "
            f"This is unexpected given fixed-size scaling — check ffmpeg output."
        )

    nframes = len(buf) // n_pixels_per_frame
    if nframes < 2:
        return np.array([]), np.array([])

    frames = (
        np.frombuffer(buf, dtype=np.uint8)
        .reshape(nframes, H, W)
    )

    # Mean absolute diff between consecutive frames.
    f     = frames.astype(np.int16)
    diffs = np.mean(np.abs(f[1:] - f[:-1]), axis=(1, 2)).astype(np.float64)
    # times[i] = midpoint in seconds between frame i and frame i+1.
    times = (np.arange(len(diffs)) + 0.5) / sample_fps

    return diffs, times


# ── Classify + build segments ─────────────────────────────────────────────────

def build_cut_segments(
    diffs: np.ndarray,
    times: np.ndarray,
    duration: float,
    config: dict,
):
    """Classify each sampled interval as static/moving and return
    (cut_segments, keep_segments).

    cut_segments  — contiguous spans to REMOVE (dead-time regions), padded
                    inward so the first/last frames of a still section are not
                    clipped mid-motion.
    keep_segments — complement within [0, duration]; what the trimmed clip keeps.
    Both are lists of (start, end) float tuples in seconds.
    """
    if len(diffs) == 0:
        return [], [(0.0, duration)]

    sample_fps    = config["sample_fps"]
    window_secs   = config["window_seconds"]
    threshold     = config["pixel_diff_threshold"]
    min_static    = config["min_static_seconds"]
    pad           = config["pad_seconds"]

    # Moving-average smoothing to reduce single-frame noise.
    window   = max(1, round(window_secs * sample_fps))
    kernel   = np.ones(window) / window
    smoothed = np.convolve(diffs, kernel, mode="same")

    static_mask = smoothed < threshold

    # Convert contiguous static runs into time intervals.
    cut_segments = []
    i = 0
    n = len(static_mask)
    while i < n:
        if static_mask[i]:
            j = i
            while j < n and static_mask[j]:
                j += 1
            # Derive wall-clock edges from the sample midpoints.
            run_start = times[i]     - 0.5 / sample_fps
            run_end   = times[j - 1] + 0.5 / sample_fps
            run_len   = run_end - run_start
            if run_len >= min_static:
                cut_start = run_start + pad
                cut_end   = run_end   - pad
                if cut_end > cut_start:
                    cut_segments.append((
                        max(0.0, cut_start),
                        min(duration, cut_end),
                    ))
            i = j
        else:
            i += 1

    # Defensive merge (overlapping / adjacent cuts from rounding).
    cut_segments = _merge_intervals(cut_segments)

    # Keep segments = complement of cuts within [0, duration].
    keep_segments = _complement(cut_segments, 0.0, duration)

    return cut_segments, keep_segments


def _merge_intervals(intervals: list) -> list:
    """Merge overlapping or adjacent (start, end) intervals."""
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = [list(intervals[0])]
    for s, e in intervals[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [tuple(iv) for iv in merged]


def _complement(cuts: list, start: float, end: float) -> list:
    """Return the gaps between *cuts* within [start, end]."""
    keeps = []
    pos = start
    for cs, ce in cuts:
        if cs > pos:
            keeps.append((pos, cs))
        pos = ce
    if pos < end:
        keeps.append((pos, end))
    return keeps


# ── Lossless trim (keep segments, -c copy) ────────────────────────────────────

def make_trimmed_clip(path: Path, keep_segments: list, out_path: Path) -> None:
    """Concatenate keep_segments from *path* losslessly into *out_path*.

    Uses the concat demuxer's inpoint/outpoint directives so no intermediate
    segment files are needed.  Output is written to MKV (Matroska) format:
    MKV stores per-packet absolute PTS and derives the reported fps from track
    metadata rather than MP4-style stts sample-duration tables, which avoids a
    container fps corruption that occurs with h264 B-frames and sparse keyframes
    when concatenating MP4 segments via copy.  Codec is -c copy throughout
    (cuts snap to keyframes — acceptable lossless trade-off).

    out_path should have a .mkv extension.  The caller (process_video) is
    responsible for using the correct extension.

    If there are no cut segments the caller passes keep_segments = [(0, dur)]
    so this always produces a usable output file.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="vm_trim_"))
    try:
        abs_src = str(path.resolve())
        list_txt = tmp_dir / "list.txt"
        with open(list_txt, "w") as lf:
            lf.write("ffconcat version 1.0\n")
            for s, e in keep_segments:
                lf.write(f"file '{abs_src}'\n")
                lf.write(f"inpoint {s:.6f}\n")
                lf.write(f"outpoint {e:.6f}\n")

        subprocess.run(
            [FFMPEG, "-y",
             "-f", "concat", "-safe", "0", "-i", str(list_txt),
             "-c", "copy", str(out_path)],
            capture_output=True, check=True,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Removed-sections timelapse ────────────────────────────────────────────────

def make_cuts_timelapse(
    path: Path,
    cut_segments: list,
    out_path: Path,
    speed: int,
) -> None:
    """Extract cut_segments, concatenate them, then speed up x *speed*.

    Re-encodes with libx264 (preview quality, crf=28 veryfast).  Falls back to
    mpeg4 if libx264 is not available in the bundled ffmpeg build.  Writes
    nothing if cut_segments is empty.
    """
    if not cut_segments:
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="vm_cuts_"))
    try:
        seg_paths = []
        for idx, (s, e) in enumerate(cut_segments):
            seg_out = tmp_dir / f"cut_{idx:04d}.mp4"
            subprocess.run(
                [FFMPEG, "-y",
                 "-ss", f"{s:.6f}", "-to", f"{e:.6f}",
                 "-i", str(path),
                 "-c", "copy", str(seg_out)],
                capture_output=True, check=True,
            )
            seg_paths.append(seg_out)

        if len(seg_paths) == 1:
            concat_out = seg_paths[0]
        else:
            list_txt = tmp_dir / "list.txt"
            with open(list_txt, "w") as lf:
                for sp in seg_paths:
                    lf.write(f"file '{sp.resolve()}'\n")
            concat_out = tmp_dir / "concat_cuts.mp4"
            subprocess.run(
                [FFMPEG, "-y",
                 "-f", "concat", "-safe", "0", "-i", str(list_txt),
                 "-c", "copy", str(concat_out)],
                capture_output=True, check=True,
            )

        # Speed up + re-encode.  Try libx264 first, fall back to mpeg4.
        if not _try_encode_timelapse(concat_out, out_path, speed, codec="libx264"):
            if not _try_encode_timelapse(concat_out, out_path, speed, codec="mpeg4"):
                print("  WARNING: timelapse encode failed with both libx264 and mpeg4.",
                      file=sys.stderr)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _try_encode_timelapse(
    src: Path, out_path: Path, speed: int, codec: str
) -> bool:
    """Attempt to speed-encode *src* into *out_path*.  Return True on success."""
    cmd = [
        FFMPEG, "-y", "-i", str(src),
        "-vf", f"setpts=PTS/{speed}",
        "-an",
        "-c:v", codec,
        "-crf", "28",
    ]
    if codec == "libx264":
        cmd += ["-preset", "veryfast"]
    cmd.append(str(out_path))

    result = subprocess.run(cmd, capture_output=True)
    success = (
        result.returncode == 0
        and out_path.exists()
        and out_path.stat().st_size > 0
    )
    return success


# ── Orchestration ─────────────────────────────────────────────────────────────

def process_video(video_arg: str, config: dict) -> dict:
    """Run the full dead-time detection pipeline for one video.

    Writes three artefacts under photo_db/motion_review/:
      clips/<video_id>_trimmed.mp4   — lossless trimmed clip
      cuts/<video_id>_removed.mp4    — timelapse of removed sections (if any)
      proposals/<video_id>.json      — machine-readable summary (atomic write)

    Returns the proposal dict.
    """
    src = resolve_video_path(video_arg, config)
    print(f"  Processing : {src}")

    meta     = probe(src)
    duration = meta.get("duration") or 0.0
    print(f"  Probe      : {duration:.2f}s  codec={meta.get('codec')}  "
          f"{meta.get('width')}x{meta.get('height')}  {meta.get('fps')} fps")

    diffs, times = sampled_frame_diffs(src, config)
    nframes = len(diffs) + 1 if len(diffs) else 0
    print(f"  Sampled    : {nframes} frames, {len(diffs)} diffs")

    cut_segments, keep_segments = build_cut_segments(diffs, times, duration, config)
    print(f"  Cuts       : {len(cut_segments)}  Keep spans: {len(keep_segments)}")

    video_id = file_id(src)

    # MKV output preserves the source fps accurately in the container metadata;
    # re-muxing to MP4 with -c copy would corrupt avg_frame_rate for h264 with
    # B-frames and sparse keyframes (stts-based vs absolute-PTS-based fps).
    trimmed_path = MOTION_DIR / "clips" / f"{video_id}_trimmed.mkv"
    tl_path      = MOTION_DIR / "cuts"  / f"{video_id}_removed.mp4"
    prop_path    = MOTION_DIR / "proposals" / f"{video_id}.json"

    # Lossless trim (always produces output).
    make_trimmed_clip(src, keep_segments, trimmed_path)
    print(f"  Trimmed    : {trimmed_path}")

    # Timelapse of removed sections (only if there are cuts).
    timelapse_path_str: str | None = None
    if cut_segments:
        make_cuts_timelapse(src, cut_segments, tl_path,
                            int(config["timelapse_speed"]))
        if tl_path.exists() and tl_path.stat().st_size > 0:
            timelapse_path_str = str(tl_path.resolve())
            print(f"  Timelapse  : {tl_path}")
        else:
            print("  Timelapse  : encode failed or empty — skipped.")
    else:
        print("  Timelapse  : no cut segments, skipped.")

    cut_total        = sum(e - s for s, e in cut_segments)
    trimmed_duration = duration - cut_total

    # Derive apple_uuid from filename stem (part before the first "_").
    stem_parts = src.stem.split("_")
    apple_uuid = stem_parts[0] if stem_parts else ""

    proposal = {
        "video_id":          video_id,
        "source_path":       str(src.resolve()),
        "apple_uuid":        apple_uuid,
        "original_duration": duration,
        "trimmed_duration":  trimmed_duration,
        "cut_segments":      [{"start": s, "end": e} for s, e in cut_segments],
        "keep_segments":     [{"start": s, "end": e} for s, e in keep_segments],
        "artifacts": {
            "trimmed":   str(trimmed_path.resolve()),
            "timelapse": timelapse_path_str,
        },
        "params":  config,
        "probe":   meta,
        "created": datetime.now(timezone.utc).isoformat(),
    }

    _atomic_write_json(prop_path, proposal)
    print(f"  Proposal   : {prop_path}")

    return proposal


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Detect dead-time (no-motion) segments in videos and produce "
            "a lossless trimmed clip plus a timelapse of the removed sections."
        )
    )
    parser.add_argument(
        "--video", action="append", metavar="PATH_OR_UUID",
        help="Path to a video file or Apple Photos UUID. May be repeated.",
    )
    parser.add_argument(
        "--probe", metavar="PATH",
        help="Probe a video file and print its metadata as JSON, then exit.",
    )
    parser.add_argument(
        "--config-only", action="store_true",
        help="Ensure config.json exists (bootstrap if needed), print it, then exit.",
    )
    args = parser.parse_args()

    config = load_config()

    if args.config_only:
        print(json.dumps(config, indent=2))
        return

    if args.probe:
        info = probe(Path(args.probe))
        print(json.dumps(info, indent=2))
        return

    if not args.video:
        parser.print_help()
        sys.exit(1)

    for v in args.video:
        print(f"\n=== {v} ===")
        try:
            prop     = process_video(v, config)
            cut_secs = sum(seg["end"] - seg["start"] for seg in prop["cut_segments"])
            print(
                f"\n  Summary:\n"
                f"    original_duration  : {prop['original_duration']:.2f}s\n"
                f"    trimmed_duration   : {prop['trimmed_duration']:.2f}s\n"
                f"    time_removed       : {cut_secs:.2f}s\n"
                f"    cut_segments       : {len(prop['cut_segments'])}\n"
                f"    trimmed artifact   : {prop['artifacts']['trimmed']}\n"
                f"    timelapse artifact : {prop['artifacts']['timelapse']}"
            )
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
