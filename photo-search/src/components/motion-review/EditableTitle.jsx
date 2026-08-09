import { useState, useRef, useEffect } from "react";

const ACCENT = "#2dd4bf";

// Click the video title to rename it. The sanitized title (echoed back by
// POST /motion-review/title) becomes the export filename and, since Photos
// names an imported asset after the file on disk, the Photos asset name too.
export default function EditableTitle({ video, onRename }) {
  const [editing, setEditing] = useState(false);
  // Only a scratch buffer while actively editing — seeded from the current
  // title the moment editing starts, never synced via an effect. Display
  // mode always reads `video.title` straight from props, so there is no
  // local copy that could go stale when the server's sanitized echo lands.
  const [draft, setDraft] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const startEditing = () => {
    setDraft(video.title || "");
    setEditing(true);
  };

  const commit = () => {
    setEditing(false);
    const typed = draft.trim();
    if (typed !== (video.title || "")) onRename(video.video_id, typed);
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          if (e.key === "Escape") { e.preventDefault(); setEditing(false); }
        }}
        placeholder={video.source_name}
        style={{
          fontSize: 18,
          fontWeight: 600,
          color: "#e5e5e5",
          background: "rgba(45,212,191,0.08)",
          border: `1px solid ${ACCENT}`,
          borderRadius: 6,
          padding: "2px 8px",
          outline: "none",
          minWidth: 200,
        }}
      />
    );
  }

  return (
    <div
      onClick={startEditing}
      title="Click to rename — this becomes the exported file's name"
      style={{
        fontSize: 18,
        fontWeight: 600,
        color: "#e5e5e5",
        cursor: "pointer",
        padding: "2px 8px",
        margin: "-2px -8px",
        borderRadius: 6,
        border: "1px solid transparent",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = "#3f6f66"; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = "transparent"; }}
    >
      {video.title || video.source_name}
    </div>
  );
}
