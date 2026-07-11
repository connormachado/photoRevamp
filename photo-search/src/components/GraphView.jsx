// GraphView.jsx
// Renders text-search results on an HTML canvas at their precomputed UMAP x/y coordinates,
// as an alternative to the grid view. Photos are fetched from the /api/graph-view endpoint,
// laid out spatially using the returned x/y fields, and drawn as circular thumbnails
// colored by cluster_id_broad. Clicking a node fires onSelectPhoto so the shared modal opens.

import { useRef, useState, useEffect } from "react";

const API = "http://localhost:5001";
const W = 920;
const H = 600;
const R = 30;
const PAD = 60;

export default function GraphView({ query, onSelectPhoto }) {
  const canvasRef = useRef(null);
  const nodesRef = useRef([]); // { photo, cx, cy, r } — used for hit-testing in handlers
  const imagesRef = useRef(new Map()); // path -> HTMLImageElement, persists across re-renders

  const [photos, setPhotos] = useState([]);
  // loadedQuery tracks which query the current photos[] correspond to.
  // isLoading is derived from the mismatch between query prop and loadedQuery,
  // so no setState call needs to happen synchronously inside the effect body.
  const [loadedQuery, setLoadedQuery] = useState("");
  const [status, setStatus] = useState("idle"); // "idle" | "ready" | "error"
  const [errorMsg, setErrorMsg] = useState("");

  // Derived: we're loading whenever the query prop differs from the query whose
  // results we've already stored. Resets automatically when query changes, without
  // any synchronous setState call in the effect body.
  const isLoading = !!(query && query.trim() && loadedQuery !== query);

  // Fetch effect — re-runs whenever the query changes.
  // Uses a cancelled flag so StrictMode double-invocation doesn't update stale state.
  // All setState calls live inside .then()/.catch() callbacks (async), never in the
  // synchronous effect body, satisfying react-hooks/set-state-in-effect.
  useEffect(() => {
    if (!query || !query.trim()) return;
    let cancelled = false;

    fetch(`${API}/api/graph-view?query=${encodeURIComponent(query)}&n=50`)
      .then(r => r.json())
      .then(data => {
        if (cancelled) return;
        if (data.error) throw new Error(data.error);
        setPhotos(data.photos || []);
        setLoadedQuery(query);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) {
          setErrorMsg("Couldn't load graph view.");
          setLoadedQuery(query); // mark this query processed so isLoading clears
          setStatus("error");
        }
      });

    return () => { cancelled = true; };
  }, [query]);

  // Draw effect — re-runs whenever the photos array changes.
  // Sets up DPR-scaled canvas, maps x/y to logical pixels, and draws nodes.
  // Images load asynchronously; each onload triggers a full redraw so nodes fill in progressively.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || photos.length === 0) return;

    // DPR scaling for crisp rendering on retina displays
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Coordinate mapping: UMAP space -> canvas logical pixels
    const xs = photos.map(p => p.x);
    const ys = photos.map(p => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanX = (maxX - minX) || 1;
    const spanY = (maxY - minY) || 1;

    const toX = x => PAD + ((x - minX) / spanX) * (W - 2 * PAD);
    const toY = y => PAD + ((maxY - y) / spanY) * (H - 2 * PAD); // flip y so UMAP "up" = screen up

    // Build nodes; center single-photo case
    const nodes = photos.map(p => ({
      photo: p,
      cx: photos.length === 1 ? W / 2 : toX(p.x),
      cy: photos.length === 1 ? H / 2 : toY(p.y),
      r: R,
    }));
    nodesRef.current = nodes;

    const redraw = () => {
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "#0a0a0a";
      ctx.fillRect(0, 0, W, H);

      for (const node of nodes) {
        const img = imagesRef.current.get(node.photo.path);
        // Hue cycles through 12 broad clusters; keeps visually distinct
        const hue = ((node.photo.cluster_id_broad ?? 0) * 360 / 12) % 360;
        const color = `hsl(${hue}, 65%, 60%)`;

        // Clip to circle, draw thumbnail or placeholder
        ctx.save();
        ctx.beginPath();
        ctx.arc(node.cx, node.cy, node.r, 0, Math.PI * 2);
        ctx.closePath();
        ctx.clip();

        if (img && img.complete && img.naturalWidth) {
          // Cover-fit: scale so the smaller dimension exactly fills the circle diameter
          const scale = Math.max((2 * node.r) / img.naturalWidth, (2 * node.r) / img.naturalHeight);
          const dw = img.naturalWidth * scale;
          const dh = img.naturalHeight * scale;
          const dx = node.cx - dw / 2;
          const dy = node.cy - dh / 2;
          ctx.drawImage(img, dx, dy, dw, dh);
        } else {
          ctx.fillStyle = "#1a1a1a";
          ctx.fillRect(node.cx - node.r, node.cy - node.r, 2 * node.r, 2 * node.r);
        }

        ctx.restore();

        // Cluster-colored ring drawn outside the clip region
        ctx.beginPath();
        ctx.arc(node.cx, node.cy, node.r, 0, Math.PI * 2);
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = color;
        ctx.stroke();
      }
    };

    // Load images — skip paths already in cache (stable across StrictMode double-invoke)
    for (const p of photos) {
      if (!imagesRef.current.has(p.path)) {
        const img = new Image();
        img.onload = redraw;
        img.src = `${API}${p.thumbnail_url}`;
        imagesRef.current.set(p.path, img);
      }
    }

    // First draw — shows placeholders while images are in flight
    redraw();
  }, [photos]);

  const handleClick = (e) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    // Map from CSS pixels to logical canvas pixels
    const mx = (e.clientX - rect.left) * (W / rect.width);
    const my = (e.clientY - rect.top) * (H / rect.height);
    const nodes = nodesRef.current;
    // Iterate in reverse so topmost (last-drawn) node wins on overlap
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (Math.hypot(mx - n.cx, my - n.cy) <= n.r) {
        onSelectPhoto(n.photo);
        break;
      }
    }
  };

  const handleMouseMove = (e) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (W / rect.width);
    const my = (e.clientY - rect.top) * (H / rect.height);
    const nodes = nodesRef.current;
    let overNode = false;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (Math.hypot(mx - n.cx, my - n.cy) <= n.r) {
        overNode = true;
        break;
      }
    }
    canvas.style.cursor = overNode ? "pointer" : "default";
  };

  return (
    <div>
      {status === "ready" && !isLoading && (
        <div style={{ color: "#555", fontSize: 13, marginBottom: 8 }}>
          {photos.length} photos · graph view
        </div>
      )}
      {isLoading && (
        <div style={{ color: "#555", fontSize: 13, marginBottom: 8 }}>
          Loading graph…
        </div>
      )}
      {status === "error" && !isLoading && (
        <div style={{ color: "#555", fontSize: 13, marginBottom: 8 }}>
          {errorMsg}
        </div>
      )}
      <canvas
        ref={canvasRef}
        onClick={handleClick}
        onMouseMove={handleMouseMove}
        style={{
          width: W,
          height: H,
          maxWidth: "100%",
          borderRadius: 12,
          border: "1px solid #2a2a2a",
          background: "#0a0a0a",
          display: "block",
        }}
      />
    </div>
  );
}
