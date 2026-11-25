import { LINE_STYLE } from "@/lib/lines";

/** RL, OL, BL, B, C, D, E, M. Line colour is a fill only; Orange's text is ink. */
export function LineBadge({ line, size = 28 }: { line: string; size?: number }) {
  const s = LINE_STYLE[line];
  if (!s) return null;
  return (
    <span
      aria-hidden="true"
      data-testid="line-badge"
      className="inline-flex shrink-0 items-center justify-center rounded-full font-extrabold"
      style={{ background: s.fill, color: s.ink, width: size, height: size, fontSize: size * 0.4, fontFamily: "var(--font-overpass)" }}
    >
      {s.code}
    </span>
  );
}
