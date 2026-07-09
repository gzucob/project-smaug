import Link from "next/link";
import { LAST_12M_SHORT, multiple, pct, price, yearOf } from "@/lib/format";
import { sectorColor, sectorMeta } from "@/lib/sectors";
import type { Analysis } from "@/lib/types";

/** Portfolio tile for one ticker; muted when no analysis has been computed. */
export function TickerCard({ ticker, sector, analysis }: { ticker: string; sector: string; analysis: Analysis | null }) {
  const color = sectorColor(sector);
  const meta = sectorMeta(sector);

  if (!analysis) {
    return (
      <div className="panel flex flex-col gap-3 p-5 opacity-60">
        <div className="flex items-center justify-between">
          <span className="nums text-lg font-bold tracking-wide text-ink-300">{ticker}</span>
          <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color, opacity: 0.5 }} />
        </div>
        <span className="text-xs text-ink-600">Ainda não computado</span>
      </div>
    );
  }

  return (
    <Link
      href={`/ticker/${ticker}`}
      className="panel panel-hover group relative flex flex-col gap-4 overflow-hidden p-5"
    >
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-[3px] opacity-70"
        style={{ background: `linear-gradient(90deg, transparent, ${color}, transparent)` }}
      />
      <div className="flex items-start justify-between">
        <div>
          <div className="nums text-xl font-bold tracking-wide text-ink-50">{ticker}</div>
          <div className="mt-0.5 text-[0.7rem] font-medium" style={{ color }}>
            {meta.label}
          </div>
        </div>
        <span className="nums rounded-md border border-white/8 px-2 py-0.5 text-[0.62rem] font-medium tracking-wide text-ink-500">
          {analysis.view === "ttm_live" ? LAST_12M_SHORT : yearOf(analysis.reference_date)}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Metric label="ROE" value={pct(analysis.indicators.roe)} />
        <Metric label="DY" value={pct(analysis.indicators.dividend_yield)} />
        <Metric label="P/L" value={multiple(analysis.indicators.pe)} />
      </div>

      <div className="mt-auto flex items-center justify-between border-t border-gold-500/8 pt-3">
        <span className="nums text-sm font-semibold text-ink-100">{price(analysis.price)}</span>
        <span className="text-xs text-ink-500 transition-colors group-hover:text-gold-300">ver análise →</span>
      </div>
    </Link>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-vault-950/50 px-2 py-1.5">
      <div className="text-[0.6rem] uppercase tracking-wide text-ink-600">{label}</div>
      <div className="nums text-sm font-semibold text-ink-100">{value}</div>
    </div>
  );
}
