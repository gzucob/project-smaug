import { gemKey, sectorColor } from "@/lib/sectors";
import type { Classification } from "@/lib/types";

/**
 * Gem-coded taxonomy chip. The dot + hue come from the gemstone system
 * (`gemKey`), while the text shows B3's real economic taxonomy: the setor
 * econômico as the chip, and "subsetor · segmento" beneath when known (they are
 * null under the CVM single-level fallback — ADR 0024).
 */
export function ClassificationBadge({ classification }: { classification: Classification }) {
  const color = sectorColor(gemKey(classification));
  const detail = [classification.subsetor, classification.segmento]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="flex flex-col gap-1">
      <span
        className="inline-flex w-fit items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold tracking-wide"
        style={{
          color,
          borderColor: `color-mix(in oklab, ${color} 35%, transparent)`,
          backgroundColor: `color-mix(in oklab, ${color} 10%, transparent)`,
        }}
      >
        <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
        {classification.setor}
      </span>
      {detail && <span className="text-[0.68rem] text-ink-500">{detail}</span>}
    </div>
  );
}
