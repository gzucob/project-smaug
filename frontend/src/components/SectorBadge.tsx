import { sectorMeta } from "@/lib/sectors";

/** Gem-coded sector chip. The dot + hue are the sector's gemstone identity. */
export function SectorBadge({ sector }: { sector: string }) {
  const m = sectorMeta(sector);
  const color = `var(${m.colorVar})`;
  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold tracking-wide"
      style={{
        color,
        borderColor: `color-mix(in oklab, ${color} 35%, transparent)`,
        backgroundColor: `color-mix(in oklab, ${color} 10%, transparent)`,
      }}
    >
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
      {m.label}
    </span>
  );
}
