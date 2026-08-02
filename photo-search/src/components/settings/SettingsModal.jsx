import { useEffect, useRef, useState } from "react";
import { SETTINGS_TABS } from "./tabs";

// Overlay/close chrome mirrors App.jsx's photo-detail Modal (fixed dark
// overlay, centered #141414 card, click-outside via stopPropagation). Esc
// handling follows the BulkAddPad/VerdictButtons convention: a listener added
// only while open, removed on close. Scroll-lock is new here — no existing
// modal in this app needed it before.
export default function SettingsModal({ onClose }) {
  const [activeTab, setActiveTab] = useState(SETTINGS_TABS[0].id);
  const cardRef = useRef(null);

  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    cardRef.current?.focus();

    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);

    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const ActiveComponent = SETTINGS_TABS.find(t => t.id === activeTab)?.component;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        background: "rgba(0,0,0,0.9)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        ref={cardRef}
        onClick={e => e.stopPropagation()}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        style={{
          background: "#141414",
          borderRadius: 12,
          overflow: "hidden",
          maxWidth: 720,
          width: "100%",
          height: 480,
          display: "grid",
          gridTemplateColumns: "180px 1fr",
          border: "1px solid #2a2a2a",
          outline: "none",
        }}
      >
        {/* Left tab list */}
        <div style={{
          borderRight: "1px solid #2a2a2a",
          background: "#101010",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}>
          <div style={{
            color: "#666", fontSize: 11, textTransform: "uppercase",
            letterSpacing: 1, padding: "4px 10px 10px",
          }}>
            Settings
          </div>
          {SETTINGS_TABS.map(tab => {
            const active = tab.id === activeTab;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  textAlign: "left",
                  padding: "8px 10px",
                  borderRadius: 6,
                  border: "none",
                  background: active ? "rgba(129,140,248,0.15)" : "transparent",
                  color: active ? "#818cf8" : "#aaa",
                  fontSize: 13,
                  fontWeight: active ? 600 : 500,
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
              >
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Right content pane */}
        <div style={{ position: "relative", padding: 24, overflow: "auto" }}>
          <button
            onClick={onClose}
            aria-label="Close settings"
            style={{
              position: "absolute",
              top: 16,
              right: 16,
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
            }}
          >
            ✕
          </button>
          <div style={{ height: "100%" }}>
            {ActiveComponent && <ActiveComponent />}
          </div>
        </div>
      </div>
    </div>
  );
}
