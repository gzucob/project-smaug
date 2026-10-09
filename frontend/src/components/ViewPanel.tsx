import { IndicatorGrid } from "@/components/IndicatorGrid";
import { ViewBadge } from "@/components/ViewBadge";
import { yearOf } from "@/lib/format";
import type { Analysis } from "@/lib/types";

/**
 * One perspective of a ticker: provenance header + full indicator grid.
 *
 * Comparisons and drill-down statistics use only rows sharing the displayed
 * calculation contract. Historical rows remain available in the ticker history.
 */
export function ViewPanel({
  analysis,
  compare,
  history,
  ttm,
  primary = false,
}: {
  analysis: Analysis;
  /** The closed exercise the cells are measured against, when there is one. */
  compare: Analysis | null;
  history: Analysis[];
  ttm: Analysis | null;
  primary?: boolean;
}) {
  // A changed calculation contract is not a comparable statistical window.
  const sameContract = (row: Analysis) =>
    row.calculation_contract_version === analysis.calculation_contract_version;
  const comparableHistory = history.filter(sameContract);
  const comparableTtm = ttm && sameContract(ttm) ? ttm : null;
  const comparableExercise = compare && sameContract(compare) ? compare : null;
  const isTtm = analysis.view === "ttm_live";

  return (
    <article
      className={`rounded-lg border border-copy-200/10 bg-canvas-950 ${
        primary ? "panel-hover" : ""
      } flex flex-col gap-5 p-4 sm:p-5`}
    >
      {!isTtm ? (
        <>
          <header>
            <ViewBadge view={analysis.view} year={yearOf(analysis.reference_date)} />
          </header>
          <div className="hairline" />
        </>
      ) : null}

      <IndicatorGrid
        isCurrent={isTtm}
        indicators={analysis.indicators}
        indicatorContract={analysis.indicator_contract}
        compare={comparableExercise?.indicators ?? null}
        compareLabel={comparableExercise ? yearOf(comparableExercise.reference_date) : null}
        history={comparableHistory}
        ttm={comparableTtm}
      />

      {comparableHistory.length < history.length && (
        <p className="text-xs opacity-70">Histórico limitado a exercícios com a mesma metodologia.</p>
      )}
    </article>
  );
}
