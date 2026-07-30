import { HistoryTable } from "@/components/HistoryTable";
import { Sparkline } from "@/components/Sparkline";
import { DASH, pct, toNum, yearOf } from "@/lib/format";
import type { Analysis, IndicatorKey } from "@/lib/types";

const TRENDS: { key: IndicatorKey; label: string; format: (v: number | null) => string }[] = [
  { key: "roe", label: "ROE", format: (v) => (v === null ? DASH : pct(v)) },
  { key: "net_margin", label: "Margem líquida", format: (v) => (v === null ? DASH : pct(v)) },
  { key: "dividend_yield", label: "Dividend yield", format: (v) => (v === null ? DASH : pct(v)) },
];

/**
 * Closed-year history: trend sparklines over a headline figure, then the
 * year-by-year table.
 *
 * The sparklines stay hand-rolled SVG on purpose — a trend line *beside* a
 * headline number is a different job from a chart meant to be read, which is
 * what the drill-down is for. The per-year card strip that used to close this
 * section is now `HistoryTable`.
 */
export function HistoryStrip({ history }: { history: Analysis[] }) {
  const years = history.map((h) => yearOf(h.reference_date));

  return (
    <div className="flex flex-col gap-8">
      <div className="grid gap-4 sm:grid-cols-3">
        {TRENDS.map((t) => {
          const series = history.map((h) => toNum(h.indicators[t.key]));
          const latest = series[series.length - 1] ?? null;
          const first = series.find((v) => v !== null) ?? null;
          const delta = latest !== null && first !== null ? latest - first : null;
          return (
            <div key={t.key} className="panel flex flex-col gap-3 p-5">
              <div className="flex items-baseline justify-between">
                <span className="text-xs font-semibold uppercase tracking-wide text-ink-500">{t.label}</span>
                {delta !== null && (
                  <span
                    className="nums text-[0.7rem] font-semibold"
                    style={{ color: delta >= 0 ? "var(--color-up)" : "var(--color-down)" }}
                  >
                    {delta >= 0 ? "▲" : "▼"} {pct(Math.abs(delta))}
                  </span>
                )}
              </div>
              <div className="nums text-2xl font-semibold text-ink-50">{t.format(latest)}</div>
              {/* The trend line is data, so it takes the directional colour
                  like every other mark (#145); the sector hue stays on the
                  badge and the classification. */}
              <Sparkline
                values={series}
                color={delta !== null && delta < 0 ? "var(--color-down)" : "var(--color-up)"}
                width={220}
                height={44}
              />
              <div className="flex justify-between text-[0.6rem] text-ink-600">
                <span>{years[0]}</span>
                <span>{years[years.length - 1]}</span>
              </div>
            </div>
          );
        })}
      </div>

      <HistoryTable history={history} />
    </div>
  );
}
