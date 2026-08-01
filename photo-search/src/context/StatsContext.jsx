import { createContext, useContext, useState, useEffect, useCallback } from "react";

const API = "http://localhost:5001";

// Think of this Context as a global bulletin board: any component in the tree
// can read the deleted count or post a change to it, without the value being
// passed down through every parent in between.
const StatsContext = createContext(null);

const EMPTY_BREAKDOWN = { photos_exact: 0, photos_estimated: 0, climb_cutter: 0 };

export function StatsProvider({ children }) {
  const [deleted, setDeleted] = useState(0);
  // Reclaimed space, summed server-side from three sources. The breakdown is
  // carried alongside the total purely so the UI can explain where it came from.
  const [reclaimedBytes, setReclaimedBytes] = useState(0);
  const [reclaimedBreakdown, setReclaimedBreakdown] = useState(EMPTY_BREAKDOWN);
  // The per-photo estimate the server values count-only deletions at. Served by
  // /stats rather than duplicated here, so it stays tunable in one place
  // (stats.AVG_PHOTO_BYTES). 0 until the first successful fetch.
  const [avgPhotoBytes, setAvgPhotoBytes] = useState(0);

  // Fold a /stats or /stats/increment payload into state. Both endpoints return
  // the same shape, so there's one place that knows how to read it.
  const applyStats = useCallback((d) => {
    if (typeof d.deleted === "number") setDeleted(d.deleted);
    if (typeof d.reclaimed_bytes === "number") setReclaimedBytes(d.reclaimed_bytes);
    if (d.reclaimed_breakdown) setReclaimedBreakdown(d.reclaimed_breakdown);
    if (typeof d.avg_photo_bytes === "number") setAvgPhotoBytes(d.avg_photo_bytes);
  }, []);

  // Load the saved figures on mount. The Vite dev server outlives backend
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
        if (!cancelled) applyStats(d);
      } catch {
        if (!cancelled && attempt < 20) setTimeout(() => load(attempt + 1), 1500);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [applyStats]);

  // Re-read the server's numbers. Needed because Climb Cutter exports move the
  // reclaimed total server-side without going through bump().
  const refreshStats = useCallback(() => {
    fetch(`${API}/stats`)
      .then(r => r.json())
      .then(applyStats)
      .catch(() => {});
  }, [applyStats]);

  // Optimistically nudge the local count, then reconcile with the server's
  // authoritative values (which are floored at 0 server-side). Only the count is
  // guessed locally — the byte maths, including the per-photo average, stays on
  // the server so there's one definition of it.
  const bump = useCallback((delta, exactBytes) => {
    // Only a real number counts as a size. Anything else — undefined, or a click
    // event from a handler passed by reference — means "no exact size known",
    // and the server values the deletion at the average instead.
    const bytes = Number.isFinite(exactBytes) ? Math.max(0, Math.trunc(exactBytes)) : 0;
    setDeleted(prev => Math.max(0, prev + delta));
    fetch(`${API}/stats/increment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delta, exact_bytes: bytes }),
    })
      .then(r => { if (!r.ok) throw new Error("increment failed"); return r.json(); })
      .then(applyStats)
      // The write didn't land, so take the optimistic nudge back rather than
      // showing a count that silently reverts on the next reload.
      .catch(() => setDeleted(prev => Math.max(0, prev - delta)));
  }, [applyStats]);

  // `exactBytes` is optional: pass the photo's real size when it's known (the
  // /reveal response carries one), otherwise the server values it at the average.
  const incrementDeleteCount = useCallback((exactBytes) => bump(1, exactBytes), [bump]);
  const decrementDeleteCount = useCallback(() => bump(-1), [bump]);
  // Delta-capable entry point for bulk logging (e.g. "I just deleted 23").
  // Same single persistence path as the +/− buttons; always estimated.
  const addToDeleteCount = useCallback((n) => bump(n), [bump]);

  return (
    <StatsContext.Provider value={{
      deleted,
      reclaimedBytes,
      reclaimedBreakdown,
      avgPhotoBytes,
      incrementDeleteCount,
      decrementDeleteCount,
      addToDeleteCount,
      refreshStats,
    }}>
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
