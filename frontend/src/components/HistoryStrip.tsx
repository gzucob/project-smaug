import { Sparkline } from "@/components/Sparkline";
import { DASH, multiple, pct, toNum, yearOf } from "@/lib/format";
import { sectorColor } from "@/lib/sectors";
import type { Analysis, IndicatorKey } from "@/lib/types";

const TRENDS: { key: IndicatorKey; label: string; format: (v: number | null) => string }[] = [
  { key: "roe", label: "ROE", format: (v) => (v === null ? DASH : pct(v)) },
  { key: "net_margin", label: "Margem líquida", format: (v) => (v === null ? DASH : pct(v)) },
  { key: "dividend_yield", label: "Dividend yield", format: (v) => (v === null ? DASH : pct(v)) },
];

/** Closed-year history: trend sparklines + a per-year timeline. */
export function HistoryStrip({ history, sector }: { history: Analysis[]; sector: string }) {
  const color = sectorColor(sector);
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
              <Sparkline values={series} color={color} width={220} height={44} />
              <div className="flex justify-between text-[0.6rem] text-ink-600">
                <span>{years[0]}</span>
                <span>{years[years.length - 1]}</span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="overflow-x-auto">
        <div className="flex min-w-max gap-3">
          {history.map((h) => (
            <div key={h.reference_date} className="panel min-w-[140px] flex-1 p-4">
              <div className="font-display text-lg text-ink-100">{yearOf(h.reference_date)}</div>
              <div className="mt-3 flex flex-col gap-1.5 text-xs">
                <Row label="ROE" value={pct(h.indicators.roe)} />
                <Row label="Marg. líq." value={pct(h.indicators.net_margin)} />
                <Row label="P/L" value={multiple(h.indicators.pe)} />
                <Row label="DY" value={pct(h.indicators.dividend_yield)} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-ink-600">{label}</span>
      <span className="nums text-ink-100">{value}</span>
    </div>
  );
}
