import { IndicatorGrid } from "@/components/IndicatorGrid";
import { ViewBadge } from "@/components/ViewBadge";
import { dateTime, monthYear, price, toNum, yearOf } from "@/lib/format";
import { gemKey } from "@/lib/sectors";
import type { Analysis } from "@/lib/types";

/**
 * One perspective of a ticker: provenance header + full indicator grid.
 *
 * `history` and `ttm` are threaded through untouched: the grid's per-indicator
 * drill-down charts the whole series, which is a property of the ticker rather
 * than of the view being displayed here.
 */
export function ViewPanel({
  analysis,
  history,
  ttm,
  primary = false,
}: {
  analysis: Analysis;
  history: Analysis[];
  ttm: Analysis | null;
  primary?: boolean;
}) {
  const isTtm = analysis.view === "ttm_live";
  const priceMain = toNum(analysis.price);
  const priceAdjusted = toNum(analysis.price_adjusted);
  // The adjusted average is a return ruler, not a valuation one — shown only as a
  // footnote, and only when it actually differs from the price the multiples use.
  const showAdjusted =
    priceAdjusted !== null && priceMain !== null && Math.abs(priceAdjusted - priceMain) > 0.005;

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
          {showAdjusted && (
            <div className="nums text-[0.68rem] text-ink-600">
              ajustado {price(analysis.price_adjusted)}
            </div>
          )}
        </div>
      </header>

      <div className="hairline" />

      <IndicatorGrid
        indicators={analysis.indicators}
        sector={gemKey(analysis.classification)}
        history={history}
        ttm={ttm}
      />

      <footer className="mt-1 text-[0.64rem] text-ink-600">
        Calculado em {dateTime(analysis.computed_at)}
      </footer>
    </article>
  );
}
