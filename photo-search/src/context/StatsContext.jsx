import { createContext, useContext, useState, useEffect, useCallback } from "react";

const API = "http://localhost:5001";

// Think of this Context as a global bulletin board: any component in the tree
// can read the deleted count or post a change to it, without the value being
// passed down through every parent in between.
const StatsContext = createContext(null);

export function StatsProvider({ children }) {
  const [deleted, setDeleted] = useState(0);

  // Load the saved count on mount. The Vite dev server outlives backend
  // restarts, so this often runs while Flask is still booting (loading CLIP +
  // the DB) and the request fails. Retry with backoff until it answers, or the
  // display gets stuck at 0 until the first increment corrects it.
  useEffect(() => {
    let cancelled = false;
    async function load(attempt = 0) {
      try {
        const r = await fetch(`${API}/stats`);
        if (!r.ok) throw new Error("not ready");
        const d = await r.json();
        if (!cancelled && typeof d.deleted === "number") setDeleted(d.deleted);
      } catch {
        if (!cancelled && attempt < 20) setTimeout(() => load(attempt + 1), 1500);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // Optimistically nudge the local count, then reconcile with the server's
  // authoritative value (which is floored at 0 server-side).
  const bump = useCallback((delta) => {
    setDeleted(prev => Math.max(0, prev + delta));
    fetch(`${API}/stats/increment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delta }),
    })
      .then(r => r.json())
      .then(d => { if (typeof d.deleted === "number") setDeleted(d.deleted); })
      .catch(() => {});
  }, []);

  const incrementDeleteCount = useCallback(() => bump(1), [bump]);
  const decrementDeleteCount = useCallback(() => bump(-1), [bump]);
  // Delta-capable entry point for bulk logging (e.g. "I just deleted 23").
  // Same single persistence path as the +/− buttons.
  const addToDeleteCount = useCallback((n) => bump(n), [bump]);

  return (
    <StatsContext.Provider value={{ deleted, incrementDeleteCount, decrementDeleteCount, addToDeleteCount }}>
      {children}
    </StatsContext.Provider>
  );
}

// Convenience hook so components just call useStats() instead of useContext.
export function useStats() {
  const ctx = useContext(StatsContext);
  if (!ctx) throw new Error("useStats must be used inside <StatsProvider>");
  return ctx;
}

export default StatsContext;
