import { IndicatorGrid } from "@/components/IndicatorGrid";
import { ViewBadge } from "@/components/ViewBadge";
import { dateTime, monthYear, price, toNum, yearOf } from "@/lib/format";
import type { Analysis } from "@/lib/types";

/** One perspective of a ticker: provenance header + full indicator grid. */
export function ViewPanel({ analysis, primary = false }: { analysis: Analysis; primary?: boolean }) {
  const isTtm = analysis.view === "ttm_live";
  const priceMain = toNum(analysis.price);
  const priceNom = toNum(analysis.price_nominal);
  const showNominal = priceNom !== null && priceMain !== null && Math.abs(priceNom - priceMain) > 0.005;

  return (
    <article className={`panel ${primary ? "panel-hover" : ""} flex flex-col gap-5 p-6`}>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <ViewBadge view={analysis.view} year={yearOf(analysis.reference_date)} />
          <p className="mt-2 text-xs text-ink-500">
            Período de referência ·{" "}
            <span className="text-ink-400">
              {isTtm ? monthYear(analysis.reference_date) : yearOf(analysis.reference_date)}
            </span>
          </p>
        </div>

        <div className="text-right">
          <div className="nums text-2xl font-semibold text-ink-50">{price(analysis.price)}</div>
          <div className="text-[0.68rem] text-ink-500">
            {analysis.price_basis ? `base: ${analysis.price_basis}` : "preço para múltiplos"}
          </div>
          {showNominal && (
            <div className="nums text-[0.68rem] text-ink-600">nominal {price(analysis.price_nominal)}</div>
          )}
        </div>
      </header>

      <div className="hairline" />

      <IndicatorGrid indicators={analysis.indicators} sector={analysis.sector} />

      <footer className="mt-1 text-[0.64rem] text-ink-600">
        Calculado em {dateTime(analysis.computed_at)}
      </footer>
    </article>
  );
}
