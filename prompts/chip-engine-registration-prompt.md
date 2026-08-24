Do NOT commit or push. This touches code that shipped yesterday (Prompt 41) and is scoped tightly: convert ONE branch to a registry and guard the result. Do not redesign the chip system, do not add an engine, do not change any tick's behaviour.

Goal: make adding a chip engine a pure registration in every place it's registered, and make it impossible for the registries to silently disagree. Right now chips.ENGINES (validation allowlist) and chip_resolve.ENGINES (implementation) are dict entries, but chips._validate_query holds an `if engine == ...` branch for the payload shape — so a new engine is two registrations plus a code edit. Prompt 42 adds the second engine; this is the last cheap moment.

Step 0 — Inspect and report. Follow CLAUDE.md's map-consult rule first. Then:
- Read chips.py and chip_resolve.py in full. Report every place an engine name appears — I expect chips.ENGINES, chip_resolve.ENGINES, and the _validate_query branch, but if there is a fourth (a route, a frontend constant, a schema default, a test fixture) I want it named now. A conversion that misses one leaves exactly the drift this prompt exists to remove.
- Report the EXACT signature and contract of the current semantic-payload validation: what it checks (prompts[] present? non-empty? negatives[] optional? types?), what it returns or raises, and whether it is called on load, on write, or both.
- CRITICAL CONTRACT — report and preserve: chips.load() must NEVER raise. Prompt 41 fixed a defect where a hand-edited scalar ("chips": 5) raised TypeError instead of degrading, which would have killed server startup via ensure_seeded(). Report exactly how load() currently degrades on malformed input, and confirm that an UNKNOWN engine name in a hand-edited chips.json degrades rather than crashing. If it currently crashes on an unknown engine, that is a live bug and I want it named, not quietly fixed.
- Report whether the equivalence harness from Prompt 41 still exists (the one that captured ordered result-id lists for all six chips at n=24 and n=48). If it does, I want it re-run in verification. If it was a throwaway, say so and propose the cheapest equivalent.
Report, propose the exact shape of the registry and the keyset guard, and PAUSE.

Implementation:
1. Introduce QUERY_VALIDATORS as a dict parallel to the existing engine dicts, mapping engine name -> a validator for that engine's query payload. MOVE the semantic branch's logic into _validate_semantic_query and register it; do not copy it and leave the branch. The branch must be gone when you are done — two paths that both validate payloads is the failure this prompt removes.
2. Dispatch on lookup, and handle the miss explicitly: an engine with no registered validator must produce the SAME degradation behaviour that an unknown engine produces today. Do not let a lookup miss become a KeyError that reaches server startup.
3. THE KEYSET GUARD — this is the load-bearing part, not the registry. Add a test asserting that chips.ENGINES, chip_resolve.ENGINES, and QUERY_VALIDATORS have IDENTICAL key sets. It must fail if any engine is registered in one and missing from another, and the failure message must name the engine and the missing registry. Prove it has teeth: temporarily add an engine to one dict only, confirm exactly that test goes red, then remove it. A guard that isn't demonstrated is a guard that might not work.
4. Consider and REPORT (do not silently choose) whether one dict should be the single source of truth with the others derived from it, rather than three hand-maintained dicts plus a test. If derivation is clean, say so and recommend it — but do not restructure without telling me first; three dicts plus a proven guard is an acceptable answer.
5. Change nothing about any tick's selection behaviour, the dismissal path, chip_stats, or the frontend.

Pause points: none irreversible — but if the conversion turns out to require touching resolve() or any tick's parameters, STOP and show me. That means the seam is not where 41 reported it is, and I want to know before it is papered over.

Verification:
- Build + lint clean; lint shows only the known categories with no NEW category (the count creeps, the categories are the gate).
- The keyset guard is demonstrated red-then-green as described in step 3.
- EQUIVALENCE: re-run Prompt 41's ordered-result comparison for all six chips at n=24 and n=48 — every list must be byte-identical to before this change. Restart the server before the after-capture; Python does not hot-reload, and a stale process has already produced a false verification twice in this repo. If the harness is gone, capture before/after with the cheapest equivalent and say what you used.
- Hand-edit chips.json to give a chip an unknown engine name, restart the server, and confirm it degrades exactly as it did before this change rather than crashing at startup. Restore the file afterward and confirm md5 matches.
- Register a throwaway second engine end to end (a trivial one, like 41's random) touching only the three dicts, confirm it resolves, then remove it. That is the actual claim this prompt is making — prove it.
- Full suite green; exactly 1 xfail (test_edit_boundaries.py:334) and confirm it is still marked and untouched.

Tests (conditional — registry dispatch and the keyset invariant both qualify): run /write-tests on the validator registry — assert dispatch reaches the right validator, that a valid semantic payload passes exactly as before, that a malformed one is rejected the same way it was before, that an unregistered engine degrades without raising, and that load() still never raises on any hand-edited garbage. Keep it PROPORTIONATE: this is one small function and three dict entries. In your report, name which of the tests you add guard a stated promise and which would only fail if someone edited the implementation — I want that list, and I will decide what to keep.

When everything above passes: stage every file belonging to this change, then print the exact git commit command with a message you have written, and STOP. Do not commit.

Save this prompt to prompts/chip-engine-registration-prompt.md.
