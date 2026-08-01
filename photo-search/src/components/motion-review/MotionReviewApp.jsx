import { useState, useEffect, useCallback, useRef } from "react";
import VideoQueue from "./VideoQueue";
import ReviewStage from "./ReviewStage";
import VerdictButtons from "./VerdictButtons";
import { formatBytes } from "./format";
import { regionsFromCuts } from "./regions";

const API = "http://localhost:5001";
const ACCENT = "#2dd4bf";

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
  const [editedRegions, setEditedRegions] = useState([]); // live edit-boundary list for the selected video
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState(null); // {ok, message}
  const [draftSaved, setDraftSaved] = useState(false); // brief checkmark flash on the header save icon
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState(""); // one line under the queue header
  const draftSavedTimerRef = useRef(null);
  const lastVidRef = useRef(null);

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
  }, [selectedVideoId, videos]);

  // Save = export: render the kept footage, import it into Photos at the
  // original's date, reveal it. Lives here rather than in a button because both
  // the green dome and the header save icon fire it. Deliberately does NOT
  // auto-advance to the next video — the result message is worth reading, and
  // the user still has to go check the clip in Photos.
  const runExport = useCallback(async () => {
    if (!selectedVideoId || exporting) return;
    setExporting(true);
    setExportResult(null);
    try {
      const res = await fetch(`${API}/motion-review/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: selectedVideoId, regions: editedRegions }),
      });
      const data = await res.json();
      if (!res.ok) {
        setExportResult({ ok: false, message: data.error || "Export failed." });
        return;
      }
      const revealed = data.revealed && data.revealed.success;
      setExportResult({
        ok: true,
        message: revealed
          ? "Saved to Photos and revealed — original left untouched; delete it yourself once you've checked it."
          : "Saved to Photos — original left untouched; delete it yourself once you've checked it. (Couldn't auto-reveal it.)",
      });
      if (typeof data.savings_total_bytes === "number") setSavedBytes(data.savings_total_bytes);
      setVideos((prev) => prev.map((v) =>
        v.video_id === selectedVideoId
          ? {
              ...v,
              verdict: data.verdict || v.verdict,
              exported_at: data.exported_at,
              regions: data.regions || v.regions,
              cut_segments: data.cut_segments || v.cut_segments,
              keep_segments: data.keep_segments || v.keep_segments,
              trimmed_duration: data.trimmed_duration ?? v.trimmed_duration,
              edited: data.edited ?? v.edited,
            }
          : v
      ));
    } catch {
      setExportResult({ ok: false, message: "Could not reach the backend." });
    } finally {
      setExporting(false);
    }
  }, [selectedVideoId, editedRegions, exporting]);

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

  // After a verdict: badge the video, fold in any edited boundaries, update the
  // reclaimed pool, then jump to the next unreviewed video.
  const handleDecided = useCallback((data) => {
    if (typeof data.savings_total_bytes === "number") setSavedBytes(data.savings_total_bytes);
    setVideos((prev) => {
      const next = prev.map((v) =>
        v.video_id === selectedVideoId
          ? {
              ...v,
              verdict: data.verdict,
              regions: data.regions || v.regions,
              cut_segments: data.cut_segments || v.cut_segments,
              keep_segments: data.keep_segments || v.keep_segments,
              trimmed_duration: data.trimmed_duration ?? v.trimmed_duration,
              estimated_saved_bytes: data.video_saved_bytes ?? v.estimated_saved_bytes,
              edited: data.edited ?? v.edited,
            }
          : v
      );
      const nextUnreviewed = next.find((v) => v.verdict == null);
      if (nextUnreviewed) setSelectedVideoId(nextUnreviewed.video_id);
      return next;
    });
  }, [selectedVideoId]);

  return (
    <div style={{ position: "fixed", inset: 0, background: "#0a0a0a", display: "flex", flexDirection: "column", zIndex: 100 }}>
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
            <div style={{
              width: 280,
              flexShrink: 0,
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
                <div style={{ borderTop: `1px solid ${ACCENT}22`, background: "#082521" }}>
                  <VerdictButtons
                    key={selectedVideoId}
                    videoId={selectedVideoId}
                    currentVerdict={selected.verdict}
                    exportedAt={selected.exported_at}
                    onDecided={handleDecided}
                    onExport={runExport}
                    exporting={exporting}
                    exportResult={exportResult}
                  />
                </div>
              )}
            </div>
            {selected ? (
              <ReviewStage
                key={selectedVideoId}
                video={selected}
                regions={editedRegions}
                onRegionsChange={setEditedRegions}
                onSaveDraft={saveDraft}
                draftSaved={draftSaved}
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
