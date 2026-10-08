import { gemKey, sectorColor } from "@/lib/sectors";
import type { Classification } from "@/lib/types";

/** Show only the B3 segment; the sector color remains a visual classification cue. */
export function ClassificationBadge({ classification }: { classification: Classification }) {
  const segment = classification.segmento?.trim();
  if (!segment) return null;

  const color = sectorColor(gemKey(classification));
  return (
    <span
      className="inline-flex w-fit items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold tracking-wide"
      style={{
        color,
        borderColor: `color-mix(in oklab, ${color} 35%, transparent)`,
        backgroundColor: `color-mix(in oklab, ${color} 10%, transparent)`,
      }}
    >
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
      {segment}
    </span>
  );
}
