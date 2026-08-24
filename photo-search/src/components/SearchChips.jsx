// The "cull the junk" chip row shown under the search bar.
//
// The chip list is NOT defined here any more — it lives in the backend chip
// store (`photo_db/chips.json`, served by GET /chips) so that a chip is one
// saved object with one definition. `App.jsx` fetches it and passes it down.
// A chip's wire shape is {id, label, emoji, engine, query: {prompts, negatives},
// result_size, order, enabled, builtin}; only `prompts[0]` ever reaches CLIP,
// and `id` is the persisted dismissal-ledger key.
//
// `chips` is the fetched list (already enabled-only and in display order).
// `query` is the currently-active search string; the matching chip lights up.
// `onSearch` fires with the whole chip object (not just the prompt) so the
// caller can key a dismissal ledger and re-run the search on it.
export default function SearchChips({ chips = [], query, onSearch }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 14 }}>
      {chips.map(chip => {
        const active = query === chip.query.prompts[0];
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
