// Suggested "cull the junk" prompt chips shown under the search bar.
// Each chip has an emoji `label` for display, a clean `query` string that's
// actually sent to CLIP (emoji would just be noise in the embedding), and a
// stable `id`. `id` is the persisted dismissal-ledger key — renaming one
// orphans its dismissals, so only add/remove ids deliberately; rewording
// `query` is free.
// Exported so Junk Hunt mode can fire the same six queries in parallel.
export const CHIPS = [
  { id: "accidental", emoji: "📷", label: "Accidental photo", query: "accidental photo" },
  { id: "dark", emoji: "🌑", label: "Dark or underexposed", query: "dark or underexposed photo" },
  { id: "blurry", emoji: "💨", label: "Blurry or out of focus", query: "blurry or out of focus photo" },
  { id: "screenshot", emoji: "📄", label: "Screenshot or document", query: "screenshot or document" },
  { id: "receipt", emoji: "🧾", label: "Receipt or invoice", query: "receipt or invoice" },
  { id: "duplicate", emoji: "🔁", label: "Duplicate scene", query: "duplicate scene" },
];

// `query` is the currently-active search string; the matching chip lights up.
// `onSearch` fires with the whole chip object (not just the query) so the
// caller can key a dismissal ledger and re-run the search on it.
export default function SearchChips({ query, onSearch }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 14 }}>
      {CHIPS.map(chip => {
        const active = query === chip.query;
        return (
          <button
            key={chip.id}
            onClick={() => onSearch(chip)}
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
