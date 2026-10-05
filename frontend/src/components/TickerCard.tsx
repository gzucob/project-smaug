import Link from "next/link";
import { LAST_12M_SHORT, multiple, pct, price, yearOf } from "@/lib/format";
import { sectorColor, sectorMeta } from "@/lib/sectors";
import { listingSegmentLabel } from "@/lib/governance";
import type { Analysis } from "@/lib/types";

/** Portfolio tile for one ticker; muted when no analysis has been computed. */
export function TickerCard({ ticker, sector, analysis }: { ticker: string; sector: string; analysis: Analysis | null }) {
  const color = sectorColor(sector);
  const meta = sectorMeta(sector);

  if (!analysis) {
    return (
      <div className="panel flex flex-col gap-3 p-5 opacity-60">
        <div className="flex items-center justify-between">
          <span className="nums text-lg font-bold tracking-wide text-copy-300">{ticker}</span>
          <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color, opacity: 0.5 }} />
        </div>
        <span className="text-xs text-copy-600">Ainda não calculado</span>
      </div>
    );
  }

  return (
    <Link
      href={`/ticker/${ticker}`}
      className="panel panel-hover pressable group relative flex flex-col gap-4 overflow-hidden p-5"
    >
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-[3px] opacity-70"
        style={{ background: `linear-gradient(90deg, transparent, ${color}, transparent)` }}
      />
      <div className="flex items-start justify-between">
        <div>
          <div className="nums text-xl font-bold tracking-wide text-copy-50">{ticker}</div>
          <div className="mt-0.5 text-[0.7rem] font-medium" style={{ color }}>
            {meta.label}
          </div>
          <p className="mt-1 text-[0.7rem] text-copy-500">
            {listingSegmentLabel(analysis.governance?.listing_segment)}
          </p>
        </div>
        <span className="nums rounded-md border border-copy-200/10 px-2 py-0.5 text-[0.62rem] font-medium tracking-wide text-copy-500">
          {analysis.view === "ttm_live" ? LAST_12M_SHORT : yearOf(analysis.reference_date)}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Metric label="ROE" value={pct(analysis.indicators.roe)} />
        <Metric label="Dividend Yield" value={pct(analysis.indicators.dividend_yield)} />
        <Metric label="P/L" value={multiple(analysis.indicators.pe_basic)} />
      </div>

      <div className="mt-auto flex items-center justify-between border-t border-copy-200/10 pt-3">
        <span className="nums text-sm font-semibold text-copy-100">{price(analysis.price)}</span>
        <span className="text-xs text-copy-500 transition-colors group-hover:text-accent-300">abrir análise →</span>
      </div>
    </Link>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-canvas-950/70 px-2 py-1.5">
      <div className="text-[0.6rem] leading-snug text-copy-600">{label}</div>
      <div className="nums text-sm font-semibold text-copy-100">{value}</div>
    </div>
  );
}
