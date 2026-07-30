import { IndicatorChart } from "@/components/IndicatorChart";
import { LAST_12M_SHORT, toNum, yearOf } from "@/lib/format";
import { BASIS_HINT, BASIS_LABEL, valueFormatter } from "@/lib/indicators";
import type { FormatKind } from "@/lib/indicators";
import { sectorColor } from "@/lib/sectors";
import type { Analysis, IndicatorKey } from "@/lib/types";

type ChartSpec = {
  key: IndicatorKey;
  label: string;
  hint: string;
  kind: FormatKind;
  /** Set when the figure is also filed on the consolidated slice (ADR 0026). */
  totalKey?: IndicatorKey;
};

const CHARTS: ChartSpec[] = [
  { key: "revenue", label: "Receita", hint: "Receita líquida do exercício", kind: "money" },
  { key: "net_income", label: "Lucro líquido", hint: "Lucro atribuído aos controladores", kind: "money", totalKey: "net_income_total" },
  { key: "dividends", label: "Dividendos", hint: "Proventos pagos no exercício", kind: "money" },
  { key: "eps", label: "LPA", hint: "Lucro por ação", kind: "price" },
  { key: "fcf", label: "Fluxo de caixa livre", hint: "Caixa operacional − CAPEX", kind: "money" },
  { key: "roe", label: "ROE", hint: "Retorno sobre o patrimônio", kind: "pct" },
];

/**
 * Per-year bar charts of the headline figures over the closed-year history,
 * with the trailing-twelve-months window appended as a dashed ghost bar so the
 * most recent reading sits next to the trajectory that produced it — without
 * ever passing for a closed exercise.
 *
 * This stays a Server Component: only `IndicatorChart` is a client boundary,
 * and it receives plain serializable data.
 */
export function HistoryCharts({
  history,
  sector,
  ttm,
}: {
  history: Analysis[];
  sector: string;
  ttm: Analysis | null;
}) {
  const color = sectorColor(sector);
  const labels = history.map((h) => yearOf(h.reference_date));
  if (ttm) labels.push(LAST_12M_SHORT);

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {CHARTS.map((c) => {
        const values = history.map((h) => toNum(h.indicators[c.key]));
        if (ttm) values.push(toNum(ttm.indicators[c.key]));
        // The bars chart the controllers' slice; the consolidated total is
        // named beside it for the latest period, and only when it reads
        // differently there (ADR 0026).
        const latest = ttm ?? history[history.length - 1];
        const format = valueFormatter(c.kind);
        const own = latest ? toNum(latest.indicators[c.key]) : null;
        const total = c.totalKey && latest ? toNum(latest.indicators[c.totalKey]) : null;
        const showTotal =
          own !== null && total !== null && format(total) !== format(own);
        return (
          <div key={c.key} className="panel flex flex-col gap-2 p-5" title={c.hint}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                {c.label}
                {c.totalKey && (
                  <span className="ml-1.5 font-normal normal-case tracking-normal text-ink-600">
                    · {BASIS_LABEL.controllers}
                  </span>
                )}
              </span>
              {showTotal && total !== null && (
                <span className="text-[0.62rem] text-ink-600" title={BASIS_HINT.total}>
                  {BASIS_LABEL.total}{" "}
                  <span className="nums text-ink-400">{format(total)}</span>
                </span>
              )}
            </div>
            <IndicatorChart
              labels={labels}
              values={values}
              color={color}
              formatKind={c.kind}
              ghostLast={ttm !== null}
              mode="bars"
              average={null}
              height={170}
            />
          </div>
        );
      })}
    </div>
  );
}
