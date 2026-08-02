# Remove from queue (keep savings)

Do NOT commit or push.

Goal: add a per-video "Remove from queue" action for videos I'm done with (typically already exported) that frees the local working copy — with an "are you sure you want to remove the local copy?" confirm like Reject has — but PRESERVES the reclaimed-bytes/savings ledger (unlike Reject, which retracts it).

Step 0 — Inspect and report:
- Prompt 10's reject/remove flow: the confirm dialog, the ownership-aware guarded delete (owned working copies only, never originals), and exactly how reject touches the savings ledger (it RETRACTS the credited savings).
- The savings ledger / decisions.jsonl: how an exported/approved video's reclaimed bytes are recorded, and what the "retract on reject" step does — so the new action can skip precisely that step and nothing else.
- Whether the working copy persists after export today (it does, until Prompt 16's keep/discard option exists) — confirm there's a copy to remove.
Report, propose a plan, and PAUSE.

Implementation:
1. Add a per-video "Remove from queue" (a.k.a. "Done") control, DISTINCT from Reject. Offer it for finished/exported entries (or universally — decide in the plan and say which).
2. On click: a confirm popup mirroring Reject's — "Remove the local copy? Your export and reclaimed-space total stay; this only frees the working file." Cancel / Remove.
3. On confirm: drop the queue entry and delete the OWNED working copy via Prompt 10's guarded delete (owned copies only — NEVER an original).
4. Do NOT touch the savings ledger / decisions.jsonl savings credit — the reclaimed bytes stay. This is the ONLY behavioral difference from Reject; make it explicit in code so the two paths can't be confused.
5. Factor "delete owned working copy + keep ledger" as a small reusable operation — Prompt 13's bulk "purge working copies" must reuse THIS (not Reject's retracting delete), so purging to free space never wipes earned savings.

Pause points: before deleting the working copy, confirm ownership (never an original). The never-touch-original guard test already covers this — keep it green.

Verification:
- Build + lint clean.
- Export a video (savings credited) -> Remove from queue -> confirm -> the working copy is deleted, the entry is gone, and the reclaimed-bytes total is UNCHANGED.
- Reject a different video -> its savings ARE retracted (unchanged reject behavior, no regression).
- The never-touch-original guard stays green.

Tests (conditional — deletion + ledger logic): extend the savings-ledger tests to assert Remove keeps the credit while Reject retracts it; run /write-tests on the changed queue-removal path.

Save this prompt to prompts/remove-keep-savings-prompt.md.

---

## Step 0 findings (recorded before implementation)

- Reject fires two sequential requests: `POST /motion-review/decision {verdict:"reject"}`
  (records the verdict, then `_apply_savings` in `motion_review.py` pops the video from
  `savings.json` and re-mirrors the total into `stats.json` — the retraction), followed
  by `POST /motion-review/remove` (`queue_removal.remove_from_queue` — the
  ownership-guarded delete).
- `remove_from_queue` **already never touches `savings.json`** — its own docstring says
  so. The retraction lives entirely inside `_apply_savings`, called only from
  `record_decision`. So "delete owned copy, keep ledger" already exists as an isolated
  primitive; the new action just needs to call `/motion-review/remove` alone and skip
  `/motion-review/decision` — no backend changes required for the core behavior.
- Working copies are confirmed to persist after export today — neither
  `export_to_photos` nor `record_decision` unlink `source_path`.
- Scope decision: the new control is gated on `exportedAt` (only for exported entries).
  A rejected entry is already removed immediately by the existing flow, so by the time
  this control would show, the only rows left are unreviewed or exported — "remove
  without ever having a verdict" isn't a workflow this prompt asks for.
