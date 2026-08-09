import { useRef, useState } from "react";

const ACCENT = "#2dd4bf";

function fmtDur(s) {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
}

function VerdictBadge({ verdict }) {
  const map = {
    approve: { bg: "rgba(34,197,94,0.15)", fg: "#22c55e", label: "approved" },
    reject: { bg: "rgba(248,113,113,0.15)", fg: "#f87171", label: "rejected" },
  };
  const s = map[verdict] || { bg: "#1e1e1e", fg: "#666", label: "unreviewed" };
  return (
    <span style={{ padding: "2px 8px", borderRadius: 10, background: s.bg, color: s.fg, fontSize: 10, fontWeight: 700 }}>
      {s.label}
    </span>
  );
}

/**
 * The file picker is hidden and clicked through a ref — the native input is
 * unstyleable, and the browser only ever hands back bytes, never a path, so the
 * parent uploads a FormData rather than passing a filename to the backend.
 */
function AddVideoButton({ onUpload, uploading }) {
  const inputRef = useRef(null);
  const [hover, setHover] = useState(false);

  const handleChange = (e) => {
    const files = Array.from(e.target.files || []);
    // Clearing the value is what lets the SAME file be picked twice in a row —
    // otherwise `change` never fires the second time.
    e.target.value = "";
    if (files.length) onUpload(files);
  };

  return (
    <>
      <button
        onClick={() => inputRef.current?.click()}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        disabled={uploading}
        title="Add a video from your Mac — it's analysed and added to the queue"
        style={{
          padding: "4px 10px",
          borderRadius: 7,
          border: `1px solid ${ACCENT}55`,
          background: hover && !uploading ? "rgba(45,212,191,0.16)" : "rgba(45,212,191,0.08)",
          color: uploading ? "#4b7a72" : ACCENT,
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.04em",
          cursor: uploading ? "default" : "pointer",
          transition: "background 0.12s",
        }}
      >
        ＋ Add video
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        multiple
        onChange={handleChange}
        style={{ display: "none" }}
      />
    </>
  );
}

/** Left rail: the list of processed videos awaiting (or with) a review. */
export default function VideoQueue({ videos, selectedVideoId, onSelect, onUpload, uploading, uploadStatus }) {
  return (
    <div style={{
      flex: 1,
      minHeight: 0,
      overflowY: "auto",
      background: "#0a2e29",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "16px 16px 8px" }}>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "#666" }}>
          Queue · {videos.length}
        </span>
        <AddVideoButton onUpload={onUpload} uploading={uploading} />
      </div>
      {uploadStatus && (
        <div style={{ padding: "0 16px 10px", fontSize: 11, color: uploading ? ACCENT : "#666", lineHeight: 1.4 }}>
          {uploadStatus}
        </div>
      )}
      {videos.map((v) => {
        const active = v.video_id === selectedVideoId;
        return (
          <button
            key={v.video_id}
            onClick={() => onSelect(v.video_id)}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              padding: "12px 16px",
              border: "none",
              borderLeft: active ? `3px solid ${ACCENT}` : "3px solid transparent",
              background: active ? "rgba(45,212,191,0.08)" : "transparent",
              cursor: "pointer",
              transition: "all 0.12s",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <span style={{ color: active ? "#e5e5e5" : "#bbb", fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {v.title || v.source_name}
              </span>
              <VerdictBadge verdict={v.verdict} />
            </div>
            <div style={{ marginTop: 4, color: "#666", fontSize: 11, fontFamily: "monospace" }}>
              {fmtDur(v.original_duration)} → {fmtDur(v.trimmed_duration)} · {v.num_cuts} cut{v.num_cuts === 1 ? "" : "s"}
              {!v.source_exists && <span style={{ color: "#b45309" }}> · no source</span>}
            </div>
          </button>
        );
      })}
    </div>
  );
}
