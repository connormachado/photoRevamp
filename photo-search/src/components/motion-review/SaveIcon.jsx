/**
 * The canonical save (floppy disk) glyph, shared by the header save button and
 * the green save dome so the two read as the same action.
 */
export default function SaveIcon({ size = 22, color = "currentColor" }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {/* outer body with the clipped bottom-right corner */}
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
      {/* label plate */}
      <polyline points="17 21 17 13 7 13 7 21" />
      {/* shutter */}
      <polyline points="7 3 7 8 15 8" />
    </svg>
  );
}
