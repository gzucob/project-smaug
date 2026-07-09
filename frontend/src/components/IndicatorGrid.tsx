import { DASH, signOf, toNum } from "@/lib/format";
import { INDICATOR_GROUPS, specsByGroup } from "@/lib/indicators";
import type { IndicatorSpec } from "@/lib/indicators";
import { sectorColor } from "@/lib/sectors";
import type { Indicators } from "@/lib/types";

export function IndicatorGrid({
  indicators,
  sector,
}: {
  indicators: Indicators;
  sector: string;
}) {
  const accent = sectorColor(sector);
  return (
    <div className="flex flex-col gap-6">
      {INDICATOR_GROUPS.map((group) => (
        <section key={group}>
          <h4 className="mb-3 flex items-center gap-2 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-ink-500">
            <span className="h-px w-4" style={{ backgroundColor: accent }} />
            {group}
          </h4>
          <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-3">
            {specsByGroup(group).map((spec) => (
              <IndicatorCell key={spec.key} spec={spec} indicators={indicators} accent={accent} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function IndicatorCell({
  spec,
  indicators,
  accent,
}: {
  spec: IndicatorSpec;
  indicators: Indicators;
  accent: string;
}) {
  const raw = indicators[spec.key];
  const text = spec.format(raw);
  const missing = toNum(raw) === null;

  let valueColor = "var(--color-ink-50)";
  if (missing) valueColor = "var(--color-ink-600)";
  else if (spec.signed) {
    const s = signOf(raw);
    valueColor = s === "up" ? "var(--color-up)" : s === "down" ? "var(--color-down)" : "var(--color-ink-200)";
  }

  return (
    <div
      className="group relative overflow-hidden rounded-xl border border-gold-500/8 bg-vault-900/40 p-3 transition-colors hover:border-gold-500/20"
      title={spec.hint}
    >
      <span
        className="absolute inset-y-0 left-0 w-[3px] opacity-40 transition-opacity group-hover:opacity-90"
        style={{ backgroundColor: accent }}
      />
      <div className="text-[0.68rem] font-medium uppercase tracking-wide text-ink-500">
        {spec.label}
      </div>
      <div className="nums mt-1 text-lg font-semibold leading-tight" style={{ color: valueColor }}>
        {text}
      </div>
      {missing && <div className="text-[0.6rem] text-ink-600">n/d</div>}
    </div>
  );
}
