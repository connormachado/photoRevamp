import { TYPE_LIST, getType } from "./boundaryTypes";

/**
 * The edit-boundary picker: which TYPE of boundary "+ Add" drops on the timeline,
 * plus the remove affordance for whichever region is selected.
 *
 * Entirely driven by the registry in ./boundaryTypes.js — a new edit type shows
 * up here automatically, with its own colour and icon. Today that list has one
 * entry ("cut"), so this reads as a single active chip.
 */
export default function BoundaryToolbar({
  activeTypeId,
  onSelectType,
  onAdd,
  selected,
  onRemove,
  disabled = false,
}) {
  const selectedType = selected ? getType(selected.type) : null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span style={{ fontSize: 11, color: "#5eead4aa", letterSpacing: "0.05em" }}>add</span>

      {TYPE_LIST.map((type) => {
        const active = type.id === activeTypeId;
        return (
          <button
            key={type.id}
            onClick={() => onSelectType(type.id)}
            title={`${type.label} boundary`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              padding: "3px 10px",
              borderRadius: 6,
              border: `1px solid ${active ? type.color : "#ffffff1a"}`,
              background: active ? `${type.color}22` : "transparent",
              color: active ? type.color : "#5f8b83",
              fontSize: 11,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            <span>{type.icon}</span>{type.label}
          </button>
        );
      })}

      <button
        onClick={onAdd}
        disabled={disabled}
        title="Add a boundary of the selected type at the playhead (c)"
        style={{
          padding: "3px 10px",
          borderRadius: 6,
          border: "1px solid #2dd4bf55",
          background: "rgba(45,212,191,0.1)",
          color: "#2dd4bf",
          fontSize: 11,
          fontWeight: 600,
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.4 : 1,
        }}
      >
        + Add (c)
      </button>

      {selected && (
        <>
          <span style={{ color: "#3f6f66" }}>·</span>
          <span style={{ fontSize: 11, color: selectedType.color, fontFamily: "monospace" }}>
            {selectedType.icon} {selectedType.describe(selected).replace(/^\S+\s/, "")}
          </span>
          <button
            onClick={onRemove}
            title="Remove the selected boundary (delete)"
            style={{
              padding: "3px 8px",
              borderRadius: 6,
              border: `1px solid ${selectedType.color}66`,
              background: "transparent",
              color: selectedType.color,
              fontSize: 11,
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </>
      )}
    </div>
  );
}
