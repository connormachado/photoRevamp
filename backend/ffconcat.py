"""
ffconcat quoting
=================
The concat demuxer's list file is a mini script, not a data file: each
`file '<path>'` line is parsed by concatdec.c using ffmpeg-utils quoting
(`av_get_token`). Inside single quotes everything is literal and a `'` cannot
appear at all — there is no in-quote escape that keeps the quoted span free of
`'` characters, which is what every writer in this repo needs (they assert
`file '...'` with no embedded quote). A newline is worse: the parser reads the
file line-by-line, so a newline in a path starts a brand-new directive with no
possible escape.

Rather than emit an unsafe path, we refuse the handful of characters that have
no representation and route anything else through a same-content symlink
staged under a safe, deterministic name. Normal paths (the overwhelming
majority) are returned unchanged.

Dependency-free by design (no numpy/ffmpeg imports) so both `export_video.py`
and `video_motion.py` can import it without a cycle.
"""

import hashlib
import os
import re
from pathlib import Path

# No ffconcat representation exists for these — refuse rather than guess.
REFUSE = ("\n", "\r", "\x00")

# Representable in principle but we decline to emit them; stage a symlink
# under a safe alias instead.
ALIAS = ("'", "\\")

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]+$")


class UnsafeConcatPathError(ValueError):
    """A source path cannot be represented in an ffconcat list file at all."""


def _stage_alias(resolved: Path, stage_dir: Path) -> Path:
    """Symlink *resolved* under a safe, deterministic name inside *stage_dir*.

    The symlink is created by us, inside our own temp/staging dir, pointing at
    an already-resolved path we were handed to render — this is not the
    attacker-controlled-symlink shape that safe_paths.resolve_within_roots and
    queue_removal._owned_source exist to refuse (those guard against a symlink
    planted by someone else redirecting an operation we perform; here we are
    the one creating the link, purely to give a hostile-but-legitimate source
    path a clean name to be referenced by).
    """
    suffix = resolved.suffix if _SAFE_SUFFIX.match(resolved.suffix) else ""
    digest = hashlib.md5(str(resolved).encode()).hexdigest()[:8]
    alias = stage_dir / f"src_{digest}{suffix}"
    try:
        os.symlink(resolved, alias)
    except FileExistsError:
        pass
    return alias


def concat_path(src: Path, stage_dir: Path) -> str:
    """Return a path string safe to embed in an ffconcat `file '...'` line.

    Raises UnsafeConcatPathError if *src* (or, in the unreachable fallback
    case, the staged alias) contains a character with no safe representation.
    """
    resolved = src.resolve()
    text = str(resolved)
    if any(ch in text for ch in REFUSE):
        raise UnsafeConcatPathError(
            f"source path contains a character with no ffconcat representation: {resolved!r}"
        )

    if any(ch in text for ch in ALIAS):
        alias = _stage_alias(resolved, stage_dir)
        text = str(alias)
        if any(ch in text for ch in REFUSE) or any(ch in text for ch in ALIAS):
            raise UnsafeConcatPathError(
                f"staged alias is still unsafe to embed: {alias!r}"
            )

    return text


def file_line(src: Path, stage_dir: Path) -> str:
    """The full `file '...'\\n` directive line for *src*."""
    return f"file '{concat_path(src, stage_dir)}'\n"
