"""
Canned `ffmpeg -i` stderr blobs
===============================
There is no ffprobe in this project — video metadata is scraped by parsing the
stderr of `ffmpeg -i <file>` (see `video_motion.probe` and
`export_video.read_source_metadata`). These are the inputs those parsers see.

IPHONE_MOV is **real captured output**, not hand-written: it came from running
the bundled ffmpeg against `backend/test-videos/test_video_1.MOV`. That matters,
because it carries the three things the code has invariants about — a
`displaymatrix: rotation of -90.00 degrees` side-data line, the
`com.apple.quicktime.creationdate` local-wall-clock tag that is preferred over
`creation_time`, and the undecodable `apac` spatial-audio + `mebx` data streams
that force explicit `-map`. A synthetic blob would have quietly omitted them.

The MALFORMED_* blobs are deliberate: a parser that raises on any of them is a
bug, because ffmpeg really does emit truncated output when it is killed mid-probe.
"""

# Real output, bundled ffmpeg 7.1 vs. an iPhone 16 Pro Max .MOV.
IPHONE_MOV = """\
[mov,mp4,m4a,3gp,3g2,mj2 @ 0x141704080] Could not find codec parameters for stream 2 (Audio: none (apac / 0x63617061), 48000 Hz, 4 channels, 388 kb/s): unknown codec
Consider increasing the value for the 'analyzeduration' (0) and 'probesize' (5000000) options
[aist#0:2/none @ 0x14160a980] Guessed Channel Layout: 4.0
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'backend/test-videos/test_video_1.MOV':
  Metadata:
    major_brand     : qt
    minor_version   : 0
    compatible_brands: qt
    creation_time   : 2026-02-12T02:31:00.000000Z
    com.apple.quicktime.location.accuracy.horizontal: 16.073758
    com.apple.quicktime.full-frame-rate-playback-intent: 0
    com.apple.quicktime.location.ISO6709: +43.6552-072.2412+180.799/
    com.apple.quicktime.make: Apple
    com.apple.quicktime.model: iPhone 16 Pro Max
    com.apple.quicktime.software: 26.3
    com.apple.quicktime.creationdate: 2026-02-11T21:31:00-0500
  Duration: 00:00:59.18, start: 0.000000, bitrate: 23788 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, progressive), 1920x1080, 23116 kb/s, 59.97 fps, 60 tbr, 600 tbn (default)
      Metadata:
        creation_time   : 2026-02-12T02:31:00.000000Z
        handler_name    : Core Media Video
        vendor_id       : [0][0][0][0]
        encoder         : H.264
      Side data:
        displaymatrix: rotation of -90.00 degrees
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 174 kb/s (default)
      Metadata:
        creation_time   : 2026-02-12T02:31:00.000000Z
        handler_name    : Core Media Audio
  Stream #0:2[0x3](und): Audio: none (apac / 0x63617061), 48000 Hz, 4.0, 388 kb/s
  Stream #0:3[0x4](und): Data: none (mebx / 0x7862656D), 0 kb/s (default)
At least one output file must be specified
"""

# A plain screen-recording style mp4: no audio stream, no rotation, no GPS, and
# only the generic `creation_time` (no Apple local-wall-clock tag).
SILENT_MP4 = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/tmp/clip.mp4':
  Metadata:
    major_brand     : isom
    creation_time   : 2024-07-04T18:09:33.000000Z
  Duration: 00:01:04.50, start: 0.000000, bitrate: 4102 kb/s
  Stream #0:0[0x1](und): Video: h264 (Main) (avc1 / 0x31637661), yuv420p, 1280x720, 4098 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
At least one output file must be specified
"""

# Older camcorder file: variable frame rate, so ffmpeg reports no `fps` token at
# all and the parser has to fall back to `tbr`. Also uses the bare `location:`
# tag rather than the Apple ISO6709 key.
NO_FPS_ONLY_TBR = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/tmp/old.mov':
  Metadata:
    creation_time   : 2015-01-02T03:04:05.000000Z
    location        : +37.7749-122.4194/
  Duration: 00:12:00.00, start: 0.000000, bitrate: 900 kb/s
  Stream #0:0(eng): Video: mpeg4 (Simple Profile) (mp4v / 0x7634706D), yuv420p, 640x480, 890 kb/s, 25 tbr, 25 tbn
At least one output file must be specified
"""

# ── Malformed / degenerate inputs ─────────────────────────────────────────────
# Every parser must degrade to defaults on these, never raise.

MALFORMED_EMPTY = ""

MALFORMED_TRUNCATED = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/tmp/broken.mov':
  Metadata:
    creation_ti"""

MALFORMED_NO_DURATION = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/tmp/headless.mov':
  Stream #0:0: Video: h264, yuv420p, 1920x1080, 30 fps, 30 tbr
"""

MALFORMED_DURATION_NA = """\
Input #0, mpegts, from '/tmp/stream.ts':
  Duration: N/A, start: 1.400000, bitrate: N/A
  Stream #0:0: Video: h264, yuv420p, 1920x1080, 25 fps, 25 tbr
"""

MALFORMED_GARBAGE = "kernel panic: not a media file at all\n\x00\x01\x02 \xef\xbf\xbd"

MALFORMED_NOT_FOUND = """\
/tmp/does-not-exist.mov: No such file or directory
"""

ALL_MALFORMED = {
    "empty": MALFORMED_EMPTY,
    "truncated": MALFORMED_TRUNCATED,
    "no_duration": MALFORMED_NO_DURATION,
    "duration_na": MALFORMED_DURATION_NA,
    "garbage": MALFORMED_GARBAGE,
    "not_found": MALFORMED_NOT_FOUND,
}
