import { useState, useEffect, useCallback, useRef } from "react";
import VideoQueue from "./VideoQueue";
import ReviewStage from "./ReviewStage";
import VerdictButtons from "./VerdictButtons";
import CollapsiblePanel from "./CollapsiblePanel";
import { formatBytes } from "./format";
import { regionsFromCuts } from "./regions";
import { useStats } from "../../context/StatsContext";

const API = "http://localhost:5001";
const ACCENT = "#2dd4bf";
const EXPORT_POLL_MS = 1000; // a 2s-stepping bar (embed's rate) reads as broken here

// export_job.py's states, minus the ones that mean "nothing is happening".
const TERMINAL_JOB_STATES = new Set(["idle", "done", "failed"]);

/** One line of plain feedback for a finished upload, including the warning that
 *  matters most: a clip whose date/GPS tags didn't survive whatever copy the
 *  macOS picker handed over. The export stamps those from the FILE, so a
 *  stripped upload exports undated — and would otherwise fail silently. */
function summarizeUpload(results) {
  const added = results.filter((r) => r.status === "queued").length;
  const dupes = results.filter((r) => r.status === "already_queued");
  const failed = results.filter((r) => r.status === "error");
  const parts = [];

  if (added) parts.push(`Added ${added} video${added === 1 ? "" : "s"}.`);
  if (dupes.length) parts.push(`${dupes.length} already in the queue.`);
  for (const r of failed) parts.push(`✕ ${r.filename}: ${r.error}`);

  for (const r of results) {
    if (r.status === "error") continue;
    const missing = [!r.has_date && "date", !r.has_gps && "location"]
      .filter(Boolean).join(" or ");
    if (missing) parts.push(`⚠ ${r.filename} carries no ${missing} — the export reads that from the file, so the saved clip won't have it.`);
  }
  return parts.join(" ");
}

/**
 * The Climb Cutter "Motion Review" room — a full-screen takeover, decoupled from
 * the photo-search UI (own folder, own backend routes) so it could be split into
 * a standalone app later. Nothing here is destructive: it only records verdicts.
 */
export default function MotionReviewApp({ onExit }) {
  const [videos, setVideos] = useState([]);
  const [selectedVideoId, setSelectedVideoId] = useState(null);
  const [savedBytes, setSavedBytes] = useState(0);
  // A verdict moves the reclaimed total server-side without going through the
  // counter's own bump(), so the main page's copy has to be re-pulled.
  const { refreshStats } = useStats();
  const [editedRegions, setEditedRegions] = useState([]); // live edit-boundary list for the selected video
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // The one background export job's last-known status (export_job.py's
  // shape: {job_id, video_id, state, progress, result, error, ...}), or null
  // before the mount-time poll resolves. Global, not per-video — there is
  // only ever one export in flight by construction — so any UI that must not
  // "decorate the wrong clip" (VerdictButtons' progress chrome, exportResult)
  // gates on `exportJob.video_id === selectedVideoId` rather than trusting
  // this alone.
  const [exportJob, setExportJob] = useState(null);
  const [exportResult, setExportResult] = useState(null); // {ok, message}
  // job_id of the last completed job this component has already folded into
  // exportResult/loadQueue/refreshStats — a poll keeps re-delivering the same
  // terminal status every second, and this is what stops that from re-firing
  // the completion handling on every tick.
  const handledJobRef = useRef(null);
  // Covers two clicks landing in the same event-loop tick, which would both
  // read the same stale closed-over "not busy" state before either request
  // resolves. `exportJob`'s state is the longer-lived guard once the kickoff
  // itself has landed.
  const exportInFlightRef = useRef(false);
  const [draftSaved, setDraftSaved] = useState(false); // brief checkmark flash on the header save icon
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState(""); // one line under the queue header
  const draftSavedTimerRef = useRef(null);
  const lastVidRef = useRef(null);
  // Tool-rail "Analyze Motion": video_id currently being re-analysed, or null.
  // A plain awaited fetch, not a polled background job like export — a single
  // video's re-run is the same synchronous cost class as upload-time analysis.
  const [analyzingVideoId, setAnalyzingVideoId] = useState(null);
  const [analyzeError, setAnalyzeError] = useState("");
  const analyzeInFlightRef = useRef(false); // same double-click guard as exportInFlightRef
  // The user can switch videos while an analyze request is in flight; a late
  // response must not stomp a DIFFERENT video's live edits, so completion
  // handling checks this ref (not the closed-over selectedVideoId) before
  // touching editedRegions/analyzeError.
  const selectedVideoIdRef = useRef(selectedVideoId);
  useEffect(() => { selectedVideoIdRef.current = selectedVideoId; }, [selectedVideoId]);

  const loadQueue = useCallback(async () => {
    try {
      const [qRes, sRes] = await Promise.all([
        fetch(`${API}/motion-review/queue`),
        fetch(`${API}/motion-review/savings`),
      ]);
      const q = await qRes.json();
      const s = await sRes.json();
      const vids = q.videos || [];
      setVideos(vids);
      setSavedBytes(s.total_bytes || 0);
      setSelectedVideoId((prev) => prev || (vids[0] && vids[0].video_id) || null);
      return vids;
    } catch {
      setError("Could not reach the backend at " + API);
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadQueue(); }, [loadQueue]);

  const selected = videos.find((v) => v.video_id === selectedVideoId) || null;

  // Reset the editable region list only when the SELECTED video changes (so live
  // edits aren't clobbered by re-renders); seed it from that video's boundaries.
  // `regions` is the current shape; the cut_segments fallback covers a backend
  // that predates the edit-boundary registry.
  useEffect(() => {
    if (lastVidRef.current === selectedVideoId) return;
    lastVidRef.current = selectedVideoId;
    const v = videos.find((x) => x.video_id === selectedVideoId);
    setEditedRegions(v ? (v.regions || regionsFromCuts(v.cut_segments)) : []);
    setExportResult(null); // last video's outcome shouldn't linger on this one
    setDraftSaved(false);
    setAnalyzeError(""); // don't leak video A's analyze error onto video B
  }, [selectedVideoId, videos]);

  // Save = export: render the kept footage, import it into Photos at the
  // original's date, reveal it. Lives here rather than in a button because both
  // the green dome and the header save icon fire it. Deliberately does NOT
  // auto-advance to the next video — the result message is worth reading, and
  // the user still has to go check the clip in Photos.
  //
  // The request itself only KICKS OFF the export now — export_job.py runs the
  // actual render/import/reveal on a background thread and returns 202
  // immediately, so this function's job is just to store the job status the
  // poll effect below then drives to completion. It deliberately does not
  // await that completion or flip any "exporting" state back off itself; the
  // job's own `state` (surfaced via `exportJob`) owns that for as long as it
  // is non-terminal.
  const runExport = useCallback(async () => {
    const jobLive = exportJob && exportJob.video_id === selectedVideoId
      && !TERMINAL_JOB_STATES.has(exportJob.state);
    if (!selectedVideoId || exportInFlightRef.current || jobLive) return;
    exportInFlightRef.current = true;
    setExportResult(null);
    try {
      const res = await fetch(`${API}/motion-review/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: selectedVideoId, regions: editedRegions }),
      });
      const data = await res.json();
      if (res.status === 202) {
        setExportJob(data); // {job_id, video_id, state: "queued", ...}
        return;
      }
      if (res.status === 409) {
        setExportResult({ ok: false, message: data.error || "An export is already running." });
        if (data.status) setExportJob(data.status);
        return;
      }
      setExportResult({ ok: false, message: data.error || "Export failed." });
    } catch {
      setExportResult({ ok: false, message: "Could not reach the backend." });
    } finally {
      exportInFlightRef.current = false;
    }
  }, [selectedVideoId, editedRegions, exportJob]);

  // One-shot fetch on mount, same rationale as EmbedButton: a reload mid-
  // export (or one that finished while this page was closed) should resume
  // showing the right state instead of a stale "idle".
  useEffect(() => {
    fetch(`${API}/motion-review/export/status`)
      .then((r) => r.json())
      .then(setExportJob)
      .catch(() => {});
  }, []);

  const exportJobLive = Boolean(exportJob && !TERMINAL_JOB_STATES.has(exportJob.state));

  // The polling loop. 1s, not embed's 2s — a progress bar that steps once
  // every two seconds reads as broken. Cleans up on unmount and the instant
  // the job stops being live, same shape as EmbedButton's.
  useEffect(() => {
    if (!exportJobLive) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API}/motion-review/export/status`);
        setExportJob(await res.json());
      } catch {
        // transient — the next tick retries.
      }
    }, EXPORT_POLL_MS);
    return () => clearInterval(id);
  }, [exportJobLive]);

  // Completion handling, latched on job_id so a poll re-delivering the same
  // terminal status every second doesn't re-run this. Runs regardless of
  // which video is currently selected — the job may have been started
  // against a clip the user has since navigated away from, and /queue +
  // /savings are the authoritative source for its outcome either way.
  useEffect(() => {
    if (!exportJob || exportJob.job_id == null) return;
    if (exportJob.state !== "done" && exportJob.state !== "failed") return;
    if (handledJobRef.current === exportJob.job_id) return;
    handledJobRef.current = exportJob.job_id;

    if (exportJob.state === "done") {
      const result = exportJob.result || {};
      const imported = Boolean(result.imported && result.imported.success);
      const revealed = Boolean(result.revealed && result.revealed.success);
      let message;
      if (!imported) {
        // export_and_import doesn't raise on a failed Photos import — it
        // still credits savings and comes back {imported: {success: false}}
        // — so this has to be checked explicitly or a failed import reads as
        // a plain "Saved to Photos" under a poll.
        const detail = result.imported && result.imported.error;
        message = `Rendered, but importing into Photos failed${detail ? `: ${detail}` : "."} Original left untouched.`;
      } else {
        message = revealed
          ? "Saved to Photos and revealed — original left untouched; delete it yourself once you've checked it."
          : "Saved to Photos — original left untouched; delete it yourself once you've checked it. (Couldn't auto-reveal it.)";
      }
      setExportResult({ ok: imported, message });
      // /queue and /savings are authoritative and already carry every field
      // the old code hand-folded onto `videos` — no need to reshape here,
      // and it can't accidentally write the wrong row if the user switched
      // clips mid-export.
      loadQueue();
      refreshStats();
    } else {
      setExportResult({ ok: false, message: exportJob.error || "Export failed." });
    }
  }, [exportJob, loadQueue, refreshStats]);

  // Save = persist the CURRENT in-progress edit as a resumable draft, distinct
  // from export. Fires only from the header save icon. No ledger/audit write on
  // the backend, so a failed attempt is safe to ignore — the user just retries.
  const saveDraft = useCallback(async () => {
    if (!selectedVideoId) return;
    try {
      const res = await fetch(`${API}/motion-review/draft`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: selectedVideoId, regions: editedRegions }),
      });
      if (!res.ok) return;
      const data = await res.json();
      // Fold the saved draft's regions back onto `videos` — otherwise switching
      // to another video and back re-seeds `editedRegions` from the stale
      // snapshot fetched at page load, making the save look like a no-op.
      setVideos((prev) => prev.map((v) =>
        v.video_id === selectedVideoId
          ? { ...v, regions: data.regions || v.regions }
          : v
      ));
      clearTimeout(draftSavedTimerRef.current);
      setDraftSaved(true);
      draftSavedTimerRef.current = setTimeout(() => setDraftSaved(false), 2000);
    } catch {
      /* local-only tool; a failed draft save just means try again later */
    }
  }, [selectedVideoId, editedRegions]);

  // Rename a video's display/export title (the editable title in the review
  // header). Folds the server's sanitized echo back onto `videos` — the same
  // "don't trust the optimistic local value" pattern saveDraft uses for
  // regions, so a messy typed title visibly becomes the clean stored one.
  const renameTitle = useCallback(async (videoId, rawTitle) => {
    try {
      const res = await fetch(`${API}/motion-review/title`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: videoId, title: rawTitle }),
      });
      if (!res.ok) return;
      const data = await res.json();
      setVideos((prev) => prev.map((v) =>
        v.video_id === videoId ? { ...v, title: data.title } : v
      ));
    } catch {
      /* local-only tool; a failed rename just means try again */
    }
  }, []);

  // Re-run dead-time detection for the selected video (the "Analyze Motion"
  // tool-rail button). Unlike runExport this is a plain awaited fetch, not a
  // polled background job — a single video's re-run is the same synchronous
  // cost class as the existing upload-time analysis. selectedVideoIdRef (not
  // the closed-over selectedVideoId) is checked at completion time so a late
  // response for a video the user has since navigated away from can't stomp
  // whatever video is now selected.
  const analyzeMotion = useCallback(async () => {
    if (!selectedVideoId || analyzeInFlightRef.current) return;
    const targetVideoId = selectedVideoId;
    analyzeInFlightRef.current = true;
    setAnalyzingVideoId(targetVideoId);
    setAnalyzeError("");
    try {
      const res = await fetch(`${API}/motion-review/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: targetVideoId }),
      });
      const data = await res.json();
      if (!res.ok) {
        if (selectedVideoIdRef.current === targetVideoId) {
          setAnalyzeError(data.error || "Analyze failed.");
        }
        return;
      }
      // Fold the fresh per-video row into `videos` — same shape /queue
      // already produces, mirrors saveDraft's fold (see motion-review/CLAUDE.md).
      setVideos((prev) => prev.map((v) => (v.video_id === targetVideoId ? data : v)));
      // Wholesale-replace the live edit state with the FRESH proposal — same
      // derivation the "↺ reset to proposed" button already uses — so the
      // timeline visibly repopulates with the new suggestions. Only do this
      // if the user hasn't since switched to a different video.
      if (selectedVideoIdRef.current === targetVideoId) {
        setEditedRegions(data.proposed_regions || regionsFromCuts(data.proposed_cut_segments || []));
      }
    } catch {
      if (selectedVideoIdRef.current === targetVideoId) {
        setAnalyzeError("Could not reach the backend.");
      }
    } finally {
      analyzeInFlightRef.current = false;
      setAnalyzingVideoId((cur) => (cur === targetVideoId ? null : cur));
    }
  }, [selectedVideoId]);

  // Add videos from the Mac. The browser only gives us bytes (never a path), so
  // this posts multipart form data; the backend parks the file and runs the same
  // dead-time analysis the CLI does. That analysis is the slow part — roughly
  // 20s per minute of 1080p60 footage — and the request is held open for all of
  // it, which is why the button locks and a status line shows what's happening.
  const uploadVideos = useCallback(async (files) => {
    if (!files.length || uploading) return;
    setUploading(true);
    const label = files.length === 1 ? files[0].name : `${files.length} videos`;
    setUploadStatus(`Uploading and analysing ${label}…`);
    try {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      // Deliberately NO Content-Type header — the browser has to set it so it
      // can include the multipart boundary.
      const res = await fetch(`${API}/motion-review/upload`, { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) {
        setUploadStatus(data.error || "Upload failed.");
        return;
      }
      const results = data.results || [];
      await loadQueue();
      // Jump to what was just added. Required, not a nicety: list_queue sorts
      // unreviewed-first then OLDEST-created-first, so a new upload lands at the
      // bottom, and loadQueue only fills an empty selection.
      const first = results.find((r) => r.video_id && r.status !== "error");
      if (first) setSelectedVideoId(first.video_id);
      setUploadStatus(summarizeUpload(results));
    } catch {
      setUploadStatus("Could not reach the backend.");
    } finally {
      setUploading(false);
    }
  }, [uploading, loadQueue]);

  // Reject: record the verdict, THEN drop the row. Two requests on purpose —
  // /decision writes the history that outlives the entry, /remove is the
  // cleanup. Deliberately not routed through handleDecided, whose auto-advance
  // would select the very row that is about to disappear.
  const rejectAndRemove = useCallback(async (videoId) => {
    try {
      const dRes = await fetch(`${API}/motion-review/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: videoId, verdict: "reject" }),
      });
      const decision = await dRes.json();
      if (!dRes.ok) {
        setUploadStatus(decision.error || "Could not record the rejection.");
        return;
      }
      if (typeof decision.savings_total_bytes === "number") {
        setSavedBytes(decision.savings_total_bytes);
        refreshStats(); // keep the main page's reclaimed total in step
      }

      const rRes = await fetch(`${API}/motion-review/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: videoId }),
      });
      const removal = await rRes.json();
      if (!rRes.ok) {
        // The verdict landed, so the row stays and shows "rejected" — the
        // pre-teeth behaviour, which is a safe place to be stuck.
        setUploadStatus(removal.error || "Rejected, but the entry could not be removed.");
        return;
      }

      setVideos((prev) => {
        const next = prev.filter((v) => v.video_id !== videoId);
        setSelectedVideoId((cur) => {
          if (cur !== videoId) return cur;
          const unreviewed = next.find((v) => v.verdict == null);
          return unreviewed ? unreviewed.video_id : (next[0] ? next[0].video_id : null);
        });
        return next;
      });
      const freed = removal.freed_bytes
        ? ` · freed ${formatBytes(removal.freed_bytes)}`
        : "";
      setUploadStatus(`Removed ${removal.source_name}${freed}`);
    } catch {
      setUploadStatus("Could not reach the backend.");
    }
  }, [refreshStats]);

  // Remove from queue ("done" — not a reject): unlike rejectAndRemove above,
  // this fires /motion-review/remove ONLY. It deliberately never calls
  // /motion-review/decision, which is the only route that retracts a video's
  // savings credit (record_decision -> _apply_savings). Skipping that call is
  // what keeps the reclaimed-bytes total intact — don't "simplify" this by
  // routing it through rejectAndRemove or the decision endpoint.
  const removeOnly = useCallback(async (videoId) => {
    try {
      const rRes = await fetch(`${API}/motion-review/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: videoId }),
      });
      const removal = await rRes.json();
      if (!rRes.ok) {
        setUploadStatus(removal.error || "Could not remove this video from the queue.");
        return;
      }

      setVideos((prev) => {
        const next = prev.filter((v) => v.video_id !== videoId);
        setSelectedVideoId((cur) => {
          if (cur !== videoId) return cur;
          const unreviewed = next.find((v) => v.verdict == null);
          return unreviewed ? unreviewed.video_id : (next[0] ? next[0].video_id : null);
        });
        return next;
      });
      const freed = removal.freed_bytes
        ? ` · freed ${formatBytes(removal.freed_bytes)}`
        : "";
      setUploadStatus(`Removed ${removal.source_name}${freed} · savings kept`);
    } catch {
      setUploadStatus("Could not reach the backend.");
    }
  }, []);

  // Everything VerdictButtons is allowed to show is gated on the job actually
  // belonging to the CURRENTLY SELECTED video — exportJob itself is global
  // (one export anywhere, by construction), so without this a poll for a
  // clip the user has since navigated away from would decorate whatever is
  // on screen now instead.
  const jobForSelected = (exportJob && exportJob.video_id === selectedVideoId) ? exportJob : null;
  const exportingSelected = Boolean(jobForSelected && !TERMINAL_JOB_STATES.has(jobForSelected.state));
  const resultForSelected = jobForSelected ? exportResult : null;

  return (
    <div style={{ position: "fixed", inset: 0, background: "#0a0a0a", display: "flex", flexDirection: "column", overflow: "hidden", zIndex: 100 }}>
      {/* Distinct top bar — signals "different room" */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "14px 24px",
        borderBottom: `1px solid ${ACCENT}22`,
        background: "linear-gradient(90deg, rgba(45,212,191,0.06), transparent)",
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span style={{ fontSize: 18, fontWeight: 800, color: ACCENT, letterSpacing: "-0.3px" }}>
            🎬 Climb Cutter
          </span>
          <span style={{ fontSize: 13, color: "#666" }}>Motion Review</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          {/* Global reclaimed-data pool */}
          <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
            <span style={{ fontSize: 20, fontWeight: 800, color: "#22c55e", fontFamily: "monospace" }}>
              {formatBytes(savedBytes)}
            </span>
            <span style={{ fontSize: 12, color: "#4b7a5c" }}>reclaimed</span>
          </div>
          <button
            onClick={onExit}
            style={{
              padding: "7px 14px",
              borderRadius: 7,
              border: "1px solid #2a2a2a",
              background: "transparent",
              color: "#888",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            ✕ Exit to search
          </button>
        </div>
      </div>

      {/* Body: left rail (queue + verdict) + review stage */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {loading ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555" }}>
            Loading queue…
          </div>
        ) : error ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#f87171" }}>
            {error}
          </div>
        ) : (
          <>
            {/* Left rail: queue on top, big red/green verdict beneath. Rendered
                even when the queue is empty — the Add video button lives in its
                header, and that's exactly when it's needed most. */}
            <CollapsiblePanel dock="left">
              <div style={{
                width: "100%",
                height: "100%",
                minHeight: 0,
                borderRight: `1px solid ${ACCENT}22`,
                display: "flex",
                flexDirection: "column",
                background: "#0a2e29",
              }}>
                <VideoQueue
                  videos={videos}
                  selectedVideoId={selectedVideoId}
                  onSelect={setSelectedVideoId}
                  onUpload={uploadVideos}
                  uploading={uploading}
                  uploadStatus={uploadStatus}
                />
                {selected && (
                  <div style={{ flexShrink: 0, borderTop: `1px solid ${ACCENT}22`, background: "#082521" }}>
                    <VerdictButtons
                      key={selectedVideoId}
                      videoId={selectedVideoId}
                      currentVerdict={selected.verdict}
                      exportedAt={selected.exported_at}
                      owned={selected.owned}
                      sourceSizeBytes={selected.source_size_bytes}
                      onRejectAndRemove={rejectAndRemove}
                      onRemoveOnly={removeOnly}
                      onExport={runExport}
                      exporting={exportingSelected}
                      exportJob={jobForSelected}
                      exportResult={resultForSelected}
                    />
                  </div>
                )}
              </div>
            </CollapsiblePanel>
            {selected ? (
              <ReviewStage
                key={selectedVideoId}
                video={selected}
                regions={editedRegions}
                onRegionsChange={setEditedRegions}
                onSaveDraft={saveDraft}
                draftSaved={draftSaved}
                onAnalyzeMotion={analyzeMotion}
                analyzing={analyzingVideoId === selectedVideoId}
                analyzeError={analyzeError}
                onRenameTitle={renameTitle}
              />
            ) : (
              <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555", textAlign: "center", padding: 24 }}>
                Nothing in the queue yet — hit <span style={{ color: ACCENT, margin: "0 5px" }}>＋ Add video</span> to pick a clip from your Mac.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
