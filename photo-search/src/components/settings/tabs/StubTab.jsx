// Shared placeholder body for any tab not yet built. Each real tab (Prompts
// 13-19) replaces its own file's contents; this just fills the gap until then.
export default function StubTab({ label }) {
  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "#555",
        fontSize: 14,
        textAlign: "center",
        padding: 24,
      }}
    >
      <div>
        <div style={{ fontSize: 28, marginBottom: 10 }}>🚧</div>
        {label} — coming soon
      </div>
    </div>
  );
}
