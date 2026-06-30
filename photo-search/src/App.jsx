import { useState, useCallback, useRef } from "react";
import OpenInPhotosButton from "./components/OpenInPhotosButton";
import SyncButton from "./components/SyncButton";
import SearchChips, { CHIPS } from "./components/SearchChips";
import DeleteCounter from "./components/DeleteCounter";
import { StatsProvider } from "./context/StatsContext";

const API = "http://localhost:5001";

const SUGGESTIONS = [
  "golden hour sunset",
  "food and drinks",
  "group photos with friends",
  "snowy landscapes",
  "pets and animals",
  "city at night",
  "beach and ocean",
  "birthday celebrations",
];

// "All" is capped at 200 so a huge result set doesn't freeze the grid.
const COUNT_OPTIONS = [
  { label: "12", value: 12 },
  { label: "24", value: 24 },
  { label: "48", value: 48 },
  { label: "All", value: 200 },
];

function PhotoCard({ photo, onClick }) {
  const [loaded, setLoaded] = useState(false);
  const score = Math.round(photo.score * 100);

  return (
    <div
      onClick={() => onClick(photo)}
      style={{
        cursor: "pointer",
        borderRadius: 8,
        overflow: "hidden",
        background: "#111",
        position: "relative",
        aspectRatio: "1",
        transition: "transform 0.15s ease, box-shadow 0.15s ease",
      }}
      onMouseEnter={e => {
        e.currentTarget.style.transform = "scale(1.02)";
        e.currentTarget.style.boxShadow = "0 8px 32px rgba(0,0,0,0.6)";
      }}
      onMouseLeave={e => {
        e.currentTarget.style.transform = "scale(1)";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      {!loaded && (
        <div style={{
          position: "absolute", inset: 0,
          background: "linear-gradient(110deg, #1a1a1a 30%, #252525 50%, #1a1a1a 70%)",
          backgroundSize: "200% 100%",
          animation: "shimmer 1.4s infinite",
        }} />
      )}
      <img
        src={`${API}/thumbnail?path=${encodeURIComponent(photo.path)}&size=400`}
        alt={photo.filename}
        onLoad={() => setLoaded(true)}
        style={{
          width: "100%", height: "100%",
          objectFit: "cover",
          opacity: loaded ? 1 : 0,
          transition: "opacity 0.3s ease",
        }}
      />
      <div style={{
        position: "absolute", bottom: 0, left: 0, right: 0,
        padding: "20px 10px 8px",
        background: "linear-gradient(to top, rgba(0,0,0,0.75) 0%, transparent 100%)",
        opacity: 0,
        transition: "opacity 0.2s ease",
      }}
        className="card-overlay"
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end" }}>
          <span style={{ color: "#ccc", fontSize: 11, fontFamily: "monospace" }}>
            {photo.date_taken ? photo.date_taken.slice(0, 10).replace(/:/g, "-") : "no date"}
          </span>
          <span style={{
            background: score > 75 ? "#22c55e" : score > 55 ? "#eab308" : "#6b7280",
            color: "#000",
            fontSize: 10,
            fontWeight: 700,
            padding: "2px 6px",
            borderRadius: 99,
            fontFamily: "monospace",
          }}>
            {score}%
          </span>
        </div>
      </div>
    </div>
  );
}

function Modal({ photo, onClose, onSearchSimilar }) {
  if (!photo) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 100,
        background: "rgba(0,0,0,0.9)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "#141414",
          borderRadius: 12,
          overflow: "hidden",
          maxWidth: 900,
          width: "100%",
          display: "grid",
          gridTemplateColumns: "1fr 280px",
          border: "1px solid #2a2a2a",
        }}
      >
        <img
          src={`${API}/thumbnail?path=${encodeURIComponent(photo.path)}&size=900`}
          alt={photo.filename}
          style={{ width: "100%", height: "auto", maxHeight: "80vh", objectFit: "contain", background: "#0a0a0a" }}
        />
        <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <div style={{ color: "#666", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Filename</div>
            <div style={{ color: "#e5e5e5", fontSize: 13, wordBreak: "break-all", fontFamily: "monospace" }}>{photo.filename}</div>
          </div>
          {photo.date_taken && (
            <div>
              <div style={{ color: "#666", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Date Taken</div>
              <div style={{ color: "#e5e5e5", fontSize: 13 }}>{photo.date_taken}</div>
            </div>
          )}
          {photo.lat && (
            <div>
              <div style={{ color: "#666", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Location</div>
              <a
                href={`https://maps.google.com/?q=${photo.lat},${photo.lon}`}
                target="_blank"
                rel="noreferrer"
                style={{ color: "#818cf8", fontSize: 13 }}
              >
                {photo.lat}, {photo.lon} ↗
              </a>
            </div>
          )}
          <div>
            <div style={{ color: "#666", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Match Score</div>
            <div style={{ color: "#e5e5e5", fontSize: 13 }}>{Math.round(photo.score * 100)}%</div>
          </div>
          {photo.size_kb && (
            <div>
              <div style={{ color: "#666", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>File Size</div>
              <div style={{ color: "#e5e5e5", fontSize: 13 }}>{photo.size_kb} KB</div>
            </div>
          )}
          <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
            <OpenInPhotosButton id={photo.id} />
            <button
              onClick={() => { onSearchSimilar(photo); onClose(); }}
              style={{
                background: "#818cf8",
                color: "#fff",
                border: "none",
                borderRadius: 8,
                padding: "10px 16px",
                cursor: "pointer",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              Find similar photos
            </button>
            <button
              onClick={onClose}
              style={{
                background: "transparent",
                color: "#666",
                border: "1px solid #2a2a2a",
                borderRadius: 8,
                padding: "10px 16px",
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState("text"); // "text" | "image"
  const [searchLabel, setSearchLabel] = useState("");
  const [selectedPhoto, setSelectedPhoto] = useState(null);
  const [stats, setStats] = useState(null);
  const [resultCount, setResultCount] = useState(24); // how many results to fetch
  const [junkHunt, setJunkHunt] = useState(false); // viewing the merged junk queue?
  const [junkCount, setJunkCount] = useState(null); // result count for the button badge
  const fileRef = useRef();
  // Remembers how to re-run the last search, so toggling the count re-fetches it.
  const lastSearchRef = useRef(null);

  // Load stats on mount
  useState(() => {
    fetch(`${API}/stats`)
      .then(r => r.json())
      .then(d => setStats(d))
      .catch(() => {});
  });

  const searchByText = useCallback(async (q, n = 24) => {
    if (!q.trim()) return;
    setJunkHunt(false); // any direct search exits the junk queue
    lastSearchRef.current = (count) => searchByText(q, count);
    setLoading(true);
    setError("");
    setSearchLabel(`"${q}"`);
    try {
      const res = await fetch(`${API}/search/text`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, n }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setResults(data.results);
    } catch (e) {
      setError("Couldn't reach the server. Is server.py running on port 5001?");
    } finally {
      setLoading(false);
    }
  }, []);

  const searchByImage = useCallback(async (file, n = 24) => {
    setJunkHunt(false); // any direct search exits the junk queue
    lastSearchRef.current = (count) => searchByImage(file, count);
    setLoading(true);
    setError("");
    setSearchLabel(`photos similar to "${file.name}"`);
    const reader = new FileReader();
    reader.onload = async (e) => {
      const b64 = e.target.result.split(",")[1];
      try {
        const res = await fetch(`${API}/search/image`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ image_b64: b64, n }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        setResults(data.results);
      } catch (err) {
        setError("Couldn't reach the server. Is server.py running on port 5001?");
      } finally {
        setLoading(false);
      }
    };
    reader.readAsDataURL(file);
  }, []);

  const handleSearchSimilar = useCallback((photo) => {
    // Fetch the image from the server and re-submit it as an image search
    setLoading(true);
    setSearchLabel(`photos similar to "${photo.filename}"`);
    fetch(`${API}/thumbnail?path=${encodeURIComponent(photo.path)}&size=600`)
      .then(r => r.blob())
      .then(blob => {
        const file = new File([blob], photo.filename, { type: "image/jpeg" });
        searchByImage(file, resultCount);
      })
      .catch(() => setLoading(false));
  }, [searchByImage, resultCount]);

  // Change how many results to show and immediately re-run the current search.
  const changeCount = useCallback((n) => {
    setResultCount(n);
    if (lastSearchRef.current) lastSearchRef.current(n);
  }, []);

  // Clicking a chip mirrors typing its query and hitting Enter.
  const runChip = useCallback((q) => {
    setQuery(q);
    searchByText(q, resultCount);
  }, [searchByText, resultCount]);

  // Junk Hunt: fire all six chip queries at once, merge + dedupe by path, and
  // show the combined "worst photos" queue in the grid.
  const runJunkHunt = useCallback(async () => {
    setMode("text");
    setQuery("");
    setError("");
    setLoading(true);
    setSearchLabel("your library's worst photos");
    lastSearchRef.current = null; // the count toggle doesn't apply to junk hunt
    try {
      const responses = await Promise.all(
        CHIPS.map(chip =>
          fetch(`${API}/search/text`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: chip.query, n: 48 }),
          }).then(r => r.json())
        )
      );
      const byPath = new Map();
      for (const data of responses) {
        for (const photo of data.results || []) {
          if (!byPath.has(photo.path)) byPath.set(photo.path, photo);
        }
      }
      const merged = [...byPath.values()];
      setResults(merged);
      setJunkCount(merged.length);
      setJunkHunt(true);
    } catch (e) {
      setError("Couldn't reach the server. Is server.py running on port 5001?");
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <StatsProvider>
      <DeleteCounter />
      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #0a0a0a; color: #e5e5e5; font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif; }
        @keyframes shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .card-overlay { opacity: 0 !important; }
        div:hover > .card-overlay { opacity: 1 !important; }
        input:focus { outline: none; }
      `}</style>

      <div style={{ minHeight: "100vh", padding: "40px 24px" }}>
        {/* Header */}
        <div style={{ maxWidth: 960, margin: "0 auto 40px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6 }}>
            <h1 style={{
              fontSize: 28,
              fontWeight: 700,
              letterSpacing: "-0.5px",
              background: "linear-gradient(135deg, #e5e5e5 0%, #818cf8 100%)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}>
              photo memory
            </h1>
            {stats && (
              <span style={{ color: "#444", fontSize: 13, fontFamily: "monospace" }}>
                {stats.total.toLocaleString()} photos indexed
              </span>
            )}
          </div>
          <p style={{ color: "#555", fontSize: 14 }}>
            Search your entire library with natural language, or drop a photo to find similar ones.
          </p>
        </div>

        {/* Search bar */}
        <div style={{ maxWidth: 960, margin: "0 auto 32px" }}>
          {/* Mode toggle + library sync */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <div style={{ display: "flex", gap: 4 }}>
            {["text", "image"].map(m => (
              <button
                key={m}
                onClick={() => setMode(m)}
                style={{
                  padding: "6px 14px",
                  borderRadius: 6,
                  border: "1px solid",
                  borderColor: mode === m ? "#818cf8" : "#2a2a2a",
                  background: mode === m ? "rgba(129,140,248,0.1)" : "transparent",
                  color: mode === m ? "#818cf8" : "#555",
                  fontSize: 13,
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
              >
                {m === "text" ? "🔤 Text search" : "🖼 Image search"}
              </button>
            ))}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button
                onClick={runJunkHunt}
                disabled={loading}
                style={{
                  padding: "6px 14px",
                  borderRadius: 6,
                  border: "1px solid",
                  borderColor: junkHunt ? "#f59e0b" : "rgba(245,158,11,0.4)",
                  background: junkHunt ? "rgba(245,158,11,0.18)" : "rgba(245,158,11,0.08)",
                  color: "#f59e0b",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: loading ? "default" : "pointer",
                  opacity: loading ? 0.6 : 1,
                  transition: "all 0.15s",
                }}
              >
                🧹 Junk Hunt{junkHunt && junkCount != null ? ` (${junkCount} found)` : ""}
              </button>
              <SyncButton />
            </div>
          </div>

          {mode === "text" ? (
            <div style={{ display: "flex", gap: 10 }}>
              <div style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                background: "#141414",
                border: "1px solid #2a2a2a",
                borderRadius: 10,
                padding: "0 16px",
              }}>
                <span style={{ color: "#444", marginRight: 10, fontSize: 18 }}>⌕</span>
                <input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && searchByText(query, resultCount)}
                  placeholder='Try "hiking with friends" or "rainy city streets"'
                  style={{
                    flex: 1,
                    background: "transparent",
                    border: "none",
                    color: "#e5e5e5",
                    fontSize: 15,
                    padding: "14px 0",
                  }}
                />
              </div>
              <button
                onClick={() => searchByText(query, resultCount)}
                disabled={loading}
                style={{
                  padding: "0 24px",
                  background: "#818cf8",
                  color: "#fff",
                  border: "none",
                  borderRadius: 10,
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: "pointer",
                  opacity: loading ? 0.6 : 1,
                }}
              >
                Search
              </button>
            </div>
          ) : (
            <div
              onClick={() => fileRef.current?.click()}
              onDragOver={e => e.preventDefault()}
              onDrop={e => {
                e.preventDefault();
                const file = e.dataTransfer.files[0];
                if (file) searchByImage(file, resultCount);
              }}
              style={{
                border: "2px dashed #2a2a2a",
                borderRadius: 10,
                padding: "32px",
                textAlign: "center",
                cursor: "pointer",
                background: "#141414",
                transition: "border-color 0.2s",
              }}
            >
              <div style={{ fontSize: 32, marginBottom: 8 }}>📸</div>
              <div style={{ color: "#666", fontSize: 14 }}>
                Drop a photo here, or click to pick one — find every similar shot in your library
              </div>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                style={{ display: "none" }}
                onChange={e => {
                  const file = e.target.files[0];
                  if (file) searchByImage(file, resultCount);
                }}
              />
            </div>
          )}

          {/* Junk-cull prompt chips — fire a culling search on click */}
          {mode === "text" && (
            <SearchChips query={junkHunt ? "" : query} onSearch={runChip} />
          )}

          {/* Content-discovery suggestions (only before the first search) */}
          {mode === "text" && results.length === 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
              {SUGGESTIONS.map(s => (
                <button
                  key={s}
                  onClick={() => { setQuery(s); searchByText(s, resultCount); }}
                  style={{
                    padding: "5px 12px",
                    background: "#141414",
                    border: "1px solid #2a2a2a",
                    borderRadius: 99,
                    color: "#666",
                    fontSize: 12,
                    cursor: "pointer",
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Status */}
        {error && (
          <div style={{ maxWidth: 960, margin: "0 auto 24px", padding: "12px 16px", background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 8, color: "#f87171", fontSize: 13 }}>
            {error}
          </div>
        )}

        {loading && (
          <div style={{ maxWidth: 960, margin: "0 auto 24px", display: "flex", alignItems: "center", gap: 10, color: "#555", fontSize: 13 }}>
            <div style={{ width: 16, height: 16, border: "2px solid #333", borderTop: "2px solid #818cf8", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
            Searching {stats?.total?.toLocaleString()} photos…
          </div>
        )}

        {/* Results */}
        {results.length > 0 && (
          <div style={{ maxWidth: 960, margin: "0 auto" }}>
            {junkHunt && (
              <div style={{
                marginBottom: 16,
                padding: "10px 16px",
                background: "rgba(245,158,11,0.1)",
                border: "1px solid rgba(245,158,11,0.35)",
                borderRadius: 8,
                color: "#f59e0b",
                fontSize: 12,
                fontWeight: 700,
                letterSpacing: 1.5,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}>
                🧹 JUNK HUNT MODE — merged culling queue from all 6 categories. Search or pick a chip to exit.
              </div>
            )}
            <div style={{ marginBottom: 16, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
              <span style={{ color: "#555", fontSize: 13 }}>
                {results.length} results for {searchLabel}
              </span>
              <div style={{ display: junkHunt ? "none" : "flex", gap: 2, background: "#141414", border: "1px solid #2a2a2a", borderRadius: 8, padding: 3 }}>
                {COUNT_OPTIONS.map(opt => {
                  const active = resultCount === opt.value;
                  return (
                    <button
                      key={opt.label}
                      onClick={() => changeCount(opt.value)}
                      disabled={loading}
                      style={{
                        padding: "5px 12px",
                        borderRadius: 6,
                        border: "none",
                        background: active ? "rgba(129,140,248,0.15)" : "transparent",
                        color: active ? "#818cf8" : "#666",
                        fontSize: 12,
                        fontWeight: active ? 700 : 500,
                        cursor: loading ? "default" : "pointer",
                        transition: "all 0.15s",
                      }}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
              gap: 8,
            }}>
              {results.map((photo, i) => (
                <PhotoCard
                  key={`${photo.path}-${i}`}
                  photo={photo}
                  onClick={setSelectedPhoto}
                />
              ))}
            </div>
          </div>
        )}

        {!loading && results.length === 0 && searchLabel && (
          <div style={{ maxWidth: 960, margin: "0 auto", textAlign: "center", color: "#444", fontSize: 14, paddingTop: 40 }}>
            No results found for {searchLabel}
          </div>
        )}
      </div>

      <Modal
        photo={selectedPhoto}
        onClose={() => setSelectedPhoto(null)}
        onSearchSimilar={handleSearchSimilar}
      />
    </StatsProvider>
  );
}