// Suggested "cull the junk" prompt chips shown under the search bar.
// Each chip has an emoji `label` for display and a clean `query` string that's
// actually sent to CLIP (emoji would just be noise in the embedding).
// Exported so Junk Hunt mode can fire the same six queries in parallel.
export const CHIPS = [
  { emoji: "📷", label: "Accidental photo", query: "accidental photo" },
  { emoji: "🌑", label: "Dark or underexposed", query: "dark or underexposed photo" },
  { emoji: "💨", label: "Blurry or out of focus", query: "blurry or out of focus photo" },
  { emoji: "📄", label: "Screenshot or document", query: "screenshot or document" },
  { emoji: "🧾", label: "Receipt or invoice", query: "receipt or invoice" },
  { emoji: "🔁", label: "Duplicate scene", query: "duplicate scene" },
];

// `query` is the currently-active search string; the matching chip lights up.
// `onSearch` fires a search for the chip's query (same as typing + Enter).
export default function SearchChips({ query, onSearch }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 14 }}>
      {CHIPS.map(chip => {
        const active = query === chip.query;
        return (
          <button
            key={chip.query}
            onClick={() => onSearch(chip.query)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 14px",
              background: active ? "rgba(129,140,248,0.15)" : "#141414",
              border: "1px solid",
              borderColor: active ? "#818cf8" : "#2a2a2a",
              borderRadius: 99,
              color: active ? "#818cf8" : "#888",
              fontSize: 12,
              fontWeight: active ? 600 : 500,
              cursor: "pointer",
              transition: "all 0.15s",
            }}
            onMouseEnter={e => { if (!active) { e.currentTarget.style.color = "#ccc"; e.currentTarget.style.borderColor = "#3a3a3a"; } }}
            onMouseLeave={e => { if (!active) { e.currentTarget.style.color = "#888"; e.currentTarget.style.borderColor = "#2a2a2a"; } }}
          >
            <span>{chip.emoji}</span>
            {chip.label}
          </button>
        );
      })}
    </div>
  );
}
