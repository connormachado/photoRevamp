"""
Path containment for anything reachable from a request
======================================================
Two request-shaped inputs get turned into filesystem paths in this app, and both
used to do it verbatim:

* a `?path=` query string on `/thumbnail` and `/full`, handed straight to
  `Path()` and `send_file` — so any absolute path the server process could read
  was readable over HTTP;
* a `video_id` from a JSON body, concatenated into `<dir>/<video_id>.json` — so a
  `../`-laden id could steer a write or an unlink out of the motion-review tree.

Neither is a hypothetical: this server ships `Access-Control-Allow-Origin: *` and
has no authentication, so "localhost only" is not a boundary. Any page the user
visits while the app is running can call these routes and read the response.

The two guards here are deliberately different shapes, because the two inputs
are:

* `resolve_within_roots` — an ALLOWLIST. The caller supplies a real path and it
  must land inside a configured root. Resolution happens before the check, so
  `..`, a symlink pointing out of the tree, and a plain absolute path are all
  caught by the same test.
* `safe_id_component` — a DENYLIST of path syntax. An id is an opaque token that
  has no business containing a separator at all, so the answer is to reject
  rather than to sanitise: silently rewriting `../../etc/foo` into `etcfoo` would
  invent a different, valid target instead of refusing.

Logic only — routes live in server.py.
"""

import os
import re
from pathlib import Path

import config_store
from utils import DEFAULT_DB_PATH


class UnsafePathError(ValueError):
    """A request tried to reach outside the data this app is allowed to serve."""


def _default_roots() -> list[Path]:
    """The trees this app may read from.

    `PHOTO_MEMORY_ROOTS` (colon-separated, like PATH) overrides them, which is
    what makes this portable to another machine's library location — the app is
    headed for other people's computers, and a hardcoded ~/Pictures path would be
    wrong on most of them.
    """
    override = os.environ.get("PHOTO_MEMORY_ROOTS", "").strip()
    if override:
        return [Path(p).expanduser() for p in override.split(os.pathsep) if p]
    return [
        # Indexed photos are derivatives inside the Photos library bundle.
        config_store.get_library_root(),
        # Uploads, proposals, preview proxies and exports.
        Path(DEFAULT_DB_PATH),
    ]


ALLOWED_ROOTS = _default_roots()


def resolve_within_roots(raw: str, roots: list | None = None) -> Path:
    """Resolve *raw* and return it only if it sits inside an allowed root.

    Raises UnsafePathError otherwise. Note what this deliberately does NOT do:
    it never reports whether the file exists, so it cannot be used to probe the
    filesystem outside the roots.

    Both sides are fully resolved first (`strict=False`, so a not-yet-existing
    file still resolves). That is what makes a symlink *inside* the library
    pointing at /etc fail the check — `is_relative_to` alone on unresolved paths
    would happily accept it.
    """
    if not raw or not isinstance(raw, str):
        raise UnsafePathError("no path provided")
    if "\x00" in raw:
        raise UnsafePathError("path contains a null byte")

    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:      # RuntimeError = symlink loop
        raise UnsafePathError(f"path could not be resolved: {exc}") from exc

    for root in (roots if roots is not None else ALLOWED_ROOTS):
        try:
            root_resolved = Path(root).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved

    raise UnsafePathError("path is outside the photo library")


def safe_id_component(value: str) -> str:
    """Return *value* unchanged if it is safe to interpolate into a filename.

    Ids in this app are md5 hexdigests (`video_id` is md5-of-absolute-path), so
    anything carrying path syntax is malformed by definition, not merely
    suspicious. Rejecting rather than stripping is the point: `_draft_path` and
    `_review_path` build both a READ and a WRITE target from the same id, and a
    sanitised-but-different id would silently address the wrong file instead of
    failing.
    """
    if not value or not isinstance(value, str):
        raise UnsafePathError("no id provided")
    if value in (".", ".."):
        raise UnsafePathError(f"invalid id: {value!r}")
    for bad in ("/", "\\", "\x00"):
        if bad in value:
            raise UnsafePathError(f"invalid id: {value!r}")
    if value.startswith("-"):
        # Never reaches a shell, but a leading dash reads as a flag to any CLI
        # this id is later interpolated into an argument for.
        raise UnsafePathError(f"invalid id: {value!r}")
    return value


_TITLE_DISALLOWED_RE = re.compile(r"[^A-Za-z0-9 _.\-]")
_RESERVED_TITLE_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_TITLE_LENGTH = 120


def sanitize_title_component(raw: str) -> str:
    """Turn a user-typed video title into a filesystem-safe filename stem, or
    "" if nothing safe survives (the caller falls back to its own default
    naming, e.g. the video id).

    Unlike `safe_id_component`, this is a WHITELIST, not a denylist — a title
    is free text a person typed, not an opaque token, so silently dropping
    the handful of characters that would break a filename is the right
    tradeoff. Rejecting the whole title over one stray character (a colon, an
    emoji) would make the feature unusable.
    """
    if not raw or not isinstance(raw, str):
        return ""
    value = _TITLE_DISALLOWED_RE.sub("", raw)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value[:MAX_TITLE_LENGTH].rstrip()
    # "." / ".." can't survive to here: strip(" .") above already consumes a
    # string made entirely of dots (and/or spaces) down to "".
    if not value or value.lower() in _RESERVED_TITLE_STEMS:
        return ""
    return value
