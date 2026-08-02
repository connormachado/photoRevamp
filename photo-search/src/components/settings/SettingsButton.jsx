import { useState } from "react";
import SettingsModal from "./SettingsModal";

// Global settings entry point — a hamburger icon in the header, styled like
// BulkAddPad's square icon toggle so it matches the app's existing icon-button
// convention (no icon library in use; icons here are styled glyphs).
export default function SettingsButton() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="Settings"
        title="Settings"
        style={{
          width: 26,
          height: 26,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#1f1f22",
          color: "#888",
          border: "1px solid #2a2a2a",
          borderRadius: 7,
          fontSize: 13,
          cursor: "pointer",
          transition: "all 0.15s",
        }}
      >
        ☰
      </button>
      {open && <SettingsModal onClose={() => setOpen(false)} />}
    </>
  );
}
