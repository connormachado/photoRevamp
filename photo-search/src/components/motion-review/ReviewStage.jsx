import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import SyncedPanels from "./SyncedPanels";
import CutTimeline from "./CutTimeline";
import BoundaryToolbar from "./BoundaryToolbar";
import SaveIcon from "./SaveIcon";
import { fmtDur, formatBytes } from "./format";
import {
  regionsToCuts, regionsEqual, outputDuration, addRegionAt, removeRegion, regionsFromCuts,
  buildPlan,
} from "./regions";
import { DEFAULT_TYPE_ID } from "./boundaryTypes";

const ACCENT = "#2dd4bf";

// Before → after + how much was removed (green = savings vibe).
function DurationSummary({ original, trimmed, savedBytes }) {
  const removedPct = original > 0 ? Math.round((1 - trimmed / original) * 100) : 0;
  const cell = (label, value, color) => (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 24 }}>
      {cell("Original", fmtDur(original), "#e5e5e5")}
      <div style={{ fontSize: 24, color: "#444" }}>→</div>
      {cell("Trimmed", fmtDur(trimmed), ACCENT)}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 4, marginLeft: 8 }}>
        <div style={{
          padding: "4px 12px",
          borderRadius: 20,
          background: "rgba(34,197,94,0.12)",
          border: "1px solid rgba(34,197,94,0.4)",
          color: "#22c55e",
          fontSize: 14,
          fontWeight: 700,
        }}>
          −{removedPct}% shorter
        </div>
        {savedBytes > 0 && (
          <span style={{ color: "#22c55e", fontSize: 12, fontWeight: 600 }}>
            ≈ {formatBytes(savedBytes)} saved for this cut
          </span>
        )}
      </div>
    </div>
  );
}

// Square save button standing to the left of the title block, sized to span both
// the title line and the stats line beneath it. Saves the CURRENT edit state as
// a draft so a reload can resume it — it does not export. The green dome in the
// left rail is the only thing that exports/approves.
function HeaderSaveButton({ onSaveDraft, justSaved }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onSaveDraft}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title="Save draft — keep this in-progress edit so it's here next time"
      style={{
        width: 52,
        height: 52,          // ≈ the combined height of the title + stats rows
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 10,
        border: `1px solid ${justSaved ? "#22c55e66" : ACCENT + "55"}`,
        background: hover ? "rgba(45,212,191,0.16)" : "rgba(45,212,191,0.08)",
        color: justSaved ? "#22c55e" : ACCENT,
        cursor: "pointer",
        transition: "background 0.12s",
      }}
    >
      {justSaved
        ? <span style={{ fontSize: 20 }}>✓</span>
        : <SaveIcon size={24} />}
    </button>
  );
}

export default function ReviewStage({ video, regions, onRegionsChange, onSaveDraft, draftSaved }) {
  // The playhead is PLACED, not hovered: it only moves on click, drag, ←/→ or
  // real playback. `preview` is the transient hover position — it moves the
  // Original panel so you can see that frame, but never the playhead itself.
  const [playhead, setPlayhead] = useState(0);
  const [preview, setPreview] = useState(null);
  const [activeTypeId, setActiveTypeId] = useState(DEFAULT_TYPE_ID);
  const [selectedId, setSelectedId] = useState(null);
  const originalRef = useRef(null);      // the Original panel's <video>, for seeking
  const playheadRef = useRef(0);         // mirror of playhead for the keydown closure

  // Where the segment panels are asked to park, kept SEPARATE from `playhead`.
  // The playhead moves continuously while something plays; this only moves when
  // the playhead is deliberately placed. Handing the live playhead to the panels
  // made the two idle ones seek ~4x a second, which starved the decoder and made
  // the playing panel choppy. `seq` rather than the time so that re-placing the
  // playhead where it already sits still counts as a request.
  const [seekTarget, setSeekTarget] = useState({ t: 0, seq: 0 });
  const seekSeqRef = useRef(0);

  const duration = video ? video.original_duration || 0 : 0;
  const fps = video && video.fps ? video.fps : 30;

  // Live-derived from the (editable) region list, so drags update instantly.
  // Regions are the source of truth; cuts/keeps are what the preview panels and
  // the savings estimate still speak — same derivation the backend runs.
  //
  // MEMOISED ON PURPOSE. These arrays are props of the preview panels, and this
  // component re-renders on every `timeupdate` of a playing panel. Rebuilding
  // them each time handed the panels a new array identity several times a
  // second, which reset their playback — see SegmentVideo's reset effect.
  //
  // `cuts` still feeds the Removed/timelapse panel and the savings estimate,
  // which only care about footage that disappears. `keeps` is now the full edit
  // PLAN — pieces carrying their own speed — so the Trimmed panel plays what the
  // export will actually produce rather than only knowing how to skip cuts.
  const cuts = useMemo(() => regionsToCuts(regions || []), [regions]);
  const keeps = useMemo(() => buildPlan(regions || [], duration), [regions, duration]);
  const trimmedDuration = outputDuration(regions || [], duration);
  const savedBytes = duration > 0 && video && video.source_size_bytes
    ? Math.round(video.source_size_bytes * (1 - trimmedDuration / duration))
    : 0;
  const proposedRegions = video
    ? (video.proposed_regions || regionsFromCuts(video.proposed_cut_segments || []))
    : [];
  const isEdited = video ? !regionsEqual(regions, proposedRegions) : false;
  const selected = (regions || []).find((r) => r.id === selectedId) || null;

  // Move the Original panel to time t without touching any state. Pausing first
  // is what makes it render that exact frame.
  const seekVideo = useCallback((t) => {
    const v = originalRef.current;
    if (!v) return;
    v.pause();
    v.currentTime = t;
  }, []);

  // PLACE the playhead: click, playhead drag, ←/→ and boundary drags all land
  // here. Snapped to a real frame so `c` cuts exactly where the line sits.
  const commitPlayhead = useCallback((t) => {
    if (!duration) return;
    const snapped = fps ? Math.round(t * fps) / fps : t;
    const clamped = Math.max(0, Math.min(duration, snapped));
    seekVideo(clamped);
    setPreview(null);
    playheadRef.current = clamped;
    setPlayhead(clamped);
    seekSeqRef.current += 1;
    setSeekTarget({ t: clamped, seq: seekSeqRef.current });
  }, [duration, fps, seekVideo]);

  // PREVIEW a frame under the cursor — video moves, playhead does not.
  const previewAt = useCallback((t) => {
    if (!duration) return;
    const clamped = Math.max(0, Math.min(duration, t));
    seekVideo(clamped);
    setPreview(clamped);
  }, [duration, seekVideo]);

  // Pointer left the bar: drop the preview and put the frame back on the playhead.
  const endPreview = useCallback(() => {
    setPreview(null);
    seekVideo(playheadRef.current);
  }, [seekVideo]);

  // Only genuine playback may move the playhead on its own. `timeupdate` also
  // fires for the seeks above and for SegmentVideo's segment-change reset, which
  // is exactly what used to yank the playhead back to 0 mid-drag.
  const onPlaybackTime = useCallback((t, playing) => {
    if (!playing) return;
    playheadRef.current = t;
    setPlayhead(t);
  }, []);

  // A panel was paused by hand: place the playhead where it stopped, which also
  // brings the other panels there. This is the whole reason the panels can stop
  // chasing each other frame by frame — the set only needs to agree when
  // something is actually standing still.
  const onPlaybackStop = useCallback((t) => {
    commitPlayhead(t);
  }, [commitPlayhead]);

  // Drop a boundary of the ACTIVE type at the playhead. If the playhead already
  // sits inside one, select that instead of stacking a second on top of it.
  const addAtPlayhead = useCallback(() => {
    const t = playheadRef.current;
    const inside = (regions || []).find((r) => t >= r.start && t <= r.end);
    if (inside) { setSelectedId(inside.id); return; }
    const next = addRegionAt(regions || [], activeTypeId, t, duration, fps);
    if (!next) return; // no room in this gap
    const added = next.find((r) => !(regions || []).some((p) => p.id === r.id));
    onRegionsChange(next);
    setSelectedId(added ? added.id : null);
  }, [regions, activeTypeId, duration, fps, onRegionsChange]);

  const removeSelected = useCallback(() => {
    if (!selectedId) return;
    onRegionsChange(removeRegion(regions || [], selectedId));
    setSelectedId(null);
  }, [regions, selectedId, onRegionsChange]);

  // Keyboard: ←/→ step ∓1 frame (shift = ∓10), c adds a boundary at the
  // playhead, delete removes the selected one, escape deselects. Guarded on the
  // event target so typing in a field never triggers an edit.
  useEffect(() => {
    if (!video || !video.source_exists) return;
    function onKey(e) {
      const el = e.target;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault(); // don't scroll the page
        const frames = e.shiftKey ? 10 : 1;
        const step = frames / fps;
        commitPlayhead(playheadRef.current + (e.key === "ArrowRight" ? step : -step));
      } else if (e.key === "c" || e.key === "C") {
        e.preventDefault();
        addAtPlayhead();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (!selectedId) return;
        e.preventDefault(); // don't let Backspace navigate back
        removeSelected();
      } else if (e.key === "Escape") {
        setSelectedId(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [video, fps, commitPlayhead, addAtPlayhead, removeSelected, selectedId]);

  if (!video) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555", background: "#0d3d37" }}>
        Select a video from the queue to review.
      </div>
    );
  }

  return (
    <div style={{ flex: 1, padding: "24px 32px", overflowY: "auto", background: "#0d3d37" }}>
      {/* Header: filename + before/after + savings */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20, gap: 16, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <HeaderSaveButton
            onSaveDraft={onSaveDraft}
            justSaved={draftSaved}
          />
          <div>
            <div style={{ fontSize: 18, fontWeight: 600, color: "#e5e5e5" }}>{video.source_name}</div>
            {/* Replaces the old video_id hash — that was internal plumbing (an MD5
                of the source path) and meant nothing on screen. */}
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 12 }}>
              <span style={{ color: "#e5e5e5", fontFamily: "monospace", fontWeight: 600 }}>
                {formatBytes(video.source_size_bytes || 0)}
              </span>
              <span style={{ color: "#3f6f66" }}>:</span>
              {/* Placeholder for a future dynamically-populated comparison. */}
              <span style={{ color: "#5f8b83", fontStyle: "italic" }}>
                your video is ~x times that of y
              </span>
            </div>
          </div>
        </div>
        <DurationSummary
          original={duration}
          trimmed={trimmedDuration}
          savedBytes={savedBytes}
        />
      </div>

      {/* Three synced panels — the Original doubles as the scrub viewer */}
      <SyncedPanels
        video={video}
        onTime={onPlaybackTime}
        videoRef={originalRef}
        cuts={cuts}
        keeps={keeps}
        seekTo={seekTarget}
        onStop={onPlaybackStop}
      />

      {/* Scrub + edit timeline + frame readout */}
      <div style={{ marginTop: 24 }}>
        <div style={{ fontSize: 11, color: "#5eead4aa", letterSpacing: "0.05em", marginBottom: 6 }}>
          click to place the playhead · drag it to scrub · hover to preview · ←/→ step 1 frame · shift+←/→ step 10 · c adds a boundary of the active type there · click a block then delete to remove
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6, gap: 16, flexWrap: "wrap" }}>
          <BoundaryToolbar
            activeTypeId={activeTypeId}
            onSelectType={setActiveTypeId}
            onAdd={addAtPlayhead}
            selected={selected}
            onRemove={removeSelected}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {isEdited && (
              <button
                onClick={() => {
                  setSelectedId(null);
                  onRegionsChange(proposedRegions);
                }}
                style={{
                  padding: "3px 10px",
                  borderRadius: 6,
                  border: "1px solid #f59e0b66",
                  background: "rgba(245,158,11,0.1)",
                  color: "#f59e0b",
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                ↺ reset to proposed
              </button>
            )}
            {/* Reads out whatever frame you're actually looking at: the hovered
                one while previewing, otherwise the placed playhead. */}
            <span style={{
              fontSize: 12,
              color: preview == null ? "#e5e5e5" : "#9ca3af",
              fontFamily: "monospace",
            }}>
              {fmtDur(preview ?? playhead)} · frame {Math.round((preview ?? playhead) * fps)}
            </span>
          </div>
        </div>
        <CutTimeline
          duration={duration}
          regions={regions}
          fps={fps}
          playhead={playhead}
          videoId={video.video_id}
          onCommit={commitPlayhead}
          onPreview={previewAt}
          onPreviewEnd={endPreview}
          onRegionsChange={onRegionsChange}
          selectedId={selectedId}
          onSelectRegion={setSelectedId}
        />
      </div>
    </div>
  );
}
