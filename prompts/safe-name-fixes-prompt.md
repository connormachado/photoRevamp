Do NOT commit or push.
Goal: fix the two live defects in _safe_name that are currently marked by strict xfails — an uncapped filename length that silently rejects valid videos, and a non-ASCII stem that loses its file extension. Both xfail markers must be flipped in the SAME change, because strict=True turns an XPASS into a suite failure.

Step 0 — Inspect and report. Follow CLAUDE.md's map-consult rule first. Then:
- Read `_safe_name` in full plus the two xfail tests in test_video_upload.py — the tests already SPECIFY the intended behaviour, so treat them as the spec rather than writing a new one.
- Report the existing fallback branch that was supposed to restore a lost extension, and exactly why `"mp4"` being non-empty bypasses it. I want the mechanism named before it's changed.
- Report every caller of `_safe_name`, and where the sanitised name ends up (the on-disk filename, the queue row's `source_name`, anything else). A length cap changes stored names, so I want to know what reads them.
- Read `safe_paths.py`'s `sanitize_title_component` (shipped in Prompt 11) and report whether a length cap already exists there that should be SHARED rather than duplicated. Do not merge the two functions — they use deliberately different strategies — but don't write the same cap twice either.
- Report how collisions are currently handled, since truncating long names makes collisions more likely.
Report, propose the fix, and PAUSE.

Implementation:
1. Cap the total filename length below APFS `NAME_MAX` (255 BYTES per component — note bytes, not characters, which matters for multi-byte names). Truncate the STEM, never the extension, and leave headroom for any collision suffix the existing collision handling appends.
2. Preserve the extension independently of the stem: derive it from the original name and reattach it AFTER sanitising the stem, so a stem that transliterates to nothing (or to something that swallows the dot) can't take the extension with it. The current fallback is unreachable in this case — fix the ordering rather than adding a second fallback beside it.
3. When the sanitised stem ends up empty, fall back to a stable placeholder stem plus the preserved extension, so the row reads something sensible rather than a bare extension.
4. FLIP BOTH XFAIL MARKERS in this same change — strict=True means the suite goes RED on XPASS, so leaving them is not an option. Say in the report that you did.
5. Do not change `sanitize_title_component`'s behaviour; this prompt is scoped to `_safe_name`.

Pause points: none irreversible, but confirm before changing anything that alters names ALREADY on disk — this fix must apply to new saves only and must not rename or orphan existing working copies. If existing queue rows would be affected, stop and tell me.

Verification:
- Build + lint clean; full suite green with both markers flipped and no new xfails.
- Upload a clip named with 300+ characters: it QUEUES successfully, the on-disk name is within the limit, and the extension survives.
- Upload `日本語.mp4` and an emoji-named `.mov`: both keep their extension, and the queue row's `source_name` is not `"mp4"`.
- Two long names that truncate to the same stem both queue without one clobbering the other.
- Existing queue entries and working copies are untouched and still play.

Tests (conditional — this is exactly the kind of logic worth guarding): the two xfails become the primary assertions. Additionally run /write-tests on `_safe_name` — assert the cap is measured in BYTES for multi-byte names, that the extension survives every path including an empty sanitised stem, that truncation leaves room for a collision suffix, and that two names truncating to the same stem don't collide. Do not weaken either flipped test.

Save this prompt to prompts/safe-name-fixes-prompt.md.
