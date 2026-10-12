import { Fragment } from "react";
import { FiBarChart2, FiColumns } from "react-icons/fi";
import { IndicatorChart } from "@/components/IndicatorChart";
import type { ChartSeries } from "@/components/IndicatorChart";
import { BalanceSheetTable } from "@/components/BalanceSheetTable";
import type { BalanceSheetPeriod } from "@/components/BalanceSheetTable";
import { FinancialStatementsSection } from "@/components/FinancialStatementsSection";
import { DASH, LAST_12M_SHORT, toNum, yearOf } from "@/lib/format";
import { BASIS_HINT, BASIS_LABEL, valueFormatter } from "@/lib/indicators";
import type { FormatKind } from "@/lib/indicators";
import { sectorColor } from "@/lib/sectors";
import type { Analysis, CashDividendHistory, IndicatorKey } from "@/lib/types";

type ChartSpec = {
  key: IndicatorKey;
  label: string;
  hint: string;
  kind: FormatKind;
  /** Set when the figure is also filed on the consolidated slice (ADR 0026). */
  totalKey?: IndicatorKey;
  /**
   * The containing quantity, drawn as a hollow bar around `key`.
   *
   * Only for figures that are genuinely a part of a whole — profit out of
   * revenue, liabilities out of assets. The empty area between the two is then
   * the difference the reader is actually after (the margin, the equity), rather
   * than a subtraction of two bar heights done by eye.
   */
  envelope?: { key: IndicatorKey; label: string };
  /** Names `key`'s own series in a paired tooltip, where the card title cannot. */
  seriesLabel?: string;
  /** Show the balance sheet as independently selectable component series. */
  balanceSheet?: boolean;
};

/** Historical balance series, with statement detail below the chart. */
type ChartGroup = { charts: ChartSpec[] };

const GROUPS: ChartGroup[] = [
  {
    charts: [
      {
        key: "total_assets",
        label: "Ativos e passivos",
        hint: "Saldos patrimoniais por período; clique na legenda para ocultar ou exibir séries",
        kind: "money",
        balanceSheet: true,
      },
    ],
  },
];

/**
 * Per-year bar charts of the headline figures over the closed-year history,
 * with the trailing-twelve-months window appended as a dashed ghost bar so the
 * most recent reading sits next to the trajectory that produced it — without
 * ever passing for a closed exercise.
 *
 * This stays a Server Component: the chart and balance table are client
 * boundaries, and they receive plain serializable data.
 */
export function HistoryCharts({
  history,
  sector,
  ttm,
  balanceHistory,
  showStatements,
  cashHistory,
}: {
  history: Analysis[];
  sector: string;
  ttm: Analysis | null;
  balanceHistory: BalanceSheetPeriod[];
  showStatements: boolean;
  cashHistory: CashDividendHistory | null;
}) {
  const color = sectorColor(sector);
  const labels = history.map((h) => yearOf(h.reference_date));
  if (ttm) labels.push(LAST_12M_SHORT);
  const periods = ttm ? [...history, ttm] : history;
  const balanceSheetPeriods =
    ttm && history[history.length - 1]?.reference_date !== ttm.reference_date
      ? [...history, ttm]
      : history;
  const balanceSheetLabels = balanceSheetPeriods.map((period, index) => {
    const year = yearOf(period.reference_date);
    const previous = balanceSheetPeriods[index - 1];
    return period === ttm && previous && yearOf(previous.reference_date) === year
      ? `${year} TTM`
      : year;
  });
  const incomeYears = history.map((period) =>
    Number(period.reference_date.slice(0, 4)),
  );
  const incomeSeries: ChartSeries[] = [
    {
      key: "costs",
      label: "Custos",
      type: "bar",
      values: history.map((period) => toNum(period.indicators.costs)),
      color: "var(--color-series-costs)",
      xAxisId: "costs",
      barSize: 32,
    },
    {
      key: "revenue",
      label: "Receita líquida",
      type: "bar",
      values: history.map((period) => toNum(period.indicators.revenue)),
      color: "var(--color-gem-azure)",
      xAxisId: "revenue",
      barSize: 32,
    },
    {
      key: "net_income",
      label: "Lucro líquido",
      type: "line",
      values: history.map((period) => toNum(period.indicators.net_income)),
      color: "var(--color-up)",
      showDots: true,
      lineType: "linear",
    },
  ];
  const hasIncomeHistory = incomeSeries.some((item) =>
    item.values.some((value) => value !== null),
  );

  const groups = GROUPS.map((group) => ({
    ...group,
    // A whole section the filer's regime makes meaningless must not render — the
    // rule #33 set for the indicator groups. Leverage for a bank is the case
    // this exists for: deposits are its raw material, not borrowing. The API
    // says so itself (`null_reasons`), so nothing here restates the guard.
    charts: group.charts.filter((c) => !inapplicable(periods, c.key)),
  })).filter((group) => group.charts.length > 0);

  return (
    <div className="flex flex-col gap-20">
      {groups.map((group) => (
        <div
          key={group.charts.map((chart) => chart.key).join(":")}
          className="flex flex-col gap-8"
        >
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
            {group.charts.map((c) => {
              const chartPeriods = c.balanceSheet ? balanceSheetPeriods : periods;
              const chartLabels = c.balanceSheet ? balanceSheetLabels : labels;
              const periodYears = c.balanceSheet
                ? chartPeriods.map((period) => Number(period.reference_date.slice(0, 4)))
                : [];
              const values = chartPeriods.map((p) => toNum(p.indicators[c.key]));
              const envelope = c.envelope
                ? {
                    values: chartPeriods.map((p) => toNum(p.indicators[c.envelope!.key])),
                    label: c.envelope.label,
                  }
                : null;
              const series = c.balanceSheet ? balanceSheetSeries(chartPeriods) : null;
              const hasData = series
                ? series.some((item) => item.values.some((v) => v !== null))
                : values.some((v) => v !== null) ||
                  (envelope?.values.some((v) => v !== null) ?? false);
              // The bars chart the controllers' slice; the consolidated total is
              // named beside it for the latest period, and only when it reads
              // differently there (ADR 0026).
              const latest = chartPeriods[chartPeriods.length - 1];
              const format = valueFormatter(c.kind);
              const own = latest ? toNum(latest.indicators[c.key]) : null;
              const total = c.totalKey && latest ? toNum(latest.indicators[c.totalKey]) : null;
              const showTotal =
                own !== null && total !== null && format(total) !== format(own);
              return (
                <Fragment key={c.key}>
                  <div
                    className={`panel flex min-w-0 flex-col gap-2 p-5 ${
                      c.balanceSheet ? "lg:col-span-3" : ""
                    }`}
                    title={c.hint}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-x-3">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-canvas-800 text-accent-300" aria-hidden>
                          <FiColumns size={16} />
                        </span>
                        <span className="card-title">
                          {c.label}
                          {c.totalKey && (
                            <span className="ml-1.5 font-normal normal-case tracking-normal text-copy-600">
                              · {BASIS_LABEL.controllers}
                            </span>
                          )}
                        </span>
                      </div>
                      {showTotal && total !== null && (
                        <span className="text-[0.62rem] text-copy-600" title={BASIS_HINT.total}>
                          {BASIS_LABEL.total}{" "}
                          <span className="nums text-copy-400">{format(total)}</span>
                        </span>
                      )}
                    </div>
                    {!hasData ? (
                      <EmptySeries />
                    ) : (
                      <IndicatorChart
                        key={`${c.key}:${latest?.ticker ?? ""}`}
                        labels={chartLabels}
                        values={values}
                        color={color}
                        formatKind={c.kind}
                        ghostLast={!c.balanceSheet && ttm !== null}
                        mode="bars"
                        average={null}
                        height={c.balanceSheet ? 264 : 170}
                        envelope={envelope}
                        seriesLabel={c.seriesLabel}
                        series={series}
                        periodYears={periodYears}
                      />
                    )}
                  </div>
                  {c.balanceSheet && (
                    <>
                      <section
                        id="balanco"
                        aria-labelledby="ticker-balance-sheet-heading"
                        className="scroll-mt-32 lg:col-span-3"
                      >
                        <BalanceSheetTable history={balanceHistory} />
                      </section>
                      <section
                        aria-labelledby="ticker-profit-revenue-heading"
                        className="lg:col-span-3"
                      >
                        <div className="panel flex flex-col gap-2 p-5">
                          <h4
                            id="ticker-profit-revenue-heading"
                            className="card-title flex items-center gap-2"
                          >
                            <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-canvas-800 text-accent-300" aria-hidden>
                              <FiBarChart2 size={16} />
                            </span>
                            Histórico de lucro e receita
                          </h4>
                          {hasIncomeHistory ? (
                            <IndicatorChart
                              key={`profit-revenue:${
                                history[history.length - 1]?.reference_date ?? ""
                              }`}
                              labels={history.map((period) =>
                                yearOf(period.reference_date),
                              )}
                              values={history.map((period) =>
                                toNum(period.indicators.net_income),
                              )}
                              color={color}
                              formatKind="money"
                              ghostLast={false}
                              mode="bars"
                              average={null}
                              height={250}
                              series={incomeSeries}
                              periodYears={incomeYears}
                              frequencyLabel="ANUAL"
                            />
                          ) : (
                            <EmptySeries />
                          )}
                        </div>
                      </section>
                      {showStatements && (
                        <div className="lg:col-span-3">
                          <FinancialStatementsSection
                            history={history}
                            ttm={ttm}
                            cashHistory={cashHistory}
                          />
                        </div>
                      )}
                    </>
                  )}
                </Fragment>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function balanceSheetSeries(periods: Analysis[]): ChartSeries[] {
  const records = periods.map((period) => ({
    totalAssets: toNum(period.indicators.total_assets),
    currentAssets: toNum(period.indicators.current_assets),
    noncurrentAssets: toNum(period.indicators.noncurrent_assets),
    currentLiabilities: toNum(period.indicators.current_liabilities),
    noncurrentLiabilities: toNum(period.indicators.noncurrent_liabilities),
    equityTotal: toNum(period.indicators.equity_total),
  }));
  const series: ChartSeries[] = [
    {
      key: "total_assets",
      label: "Ativo/Passivo total",
      type: "line",
      values: records.map((row) => row.totalAssets),
      color: "var(--color-gem-violet)",
    },
    {
      key: "equity_total",
      label: "Patrimônio líquido consolidado",
      type: "bar",
      values: records.map((row) => row.equityTotal),
      color: "var(--color-gem-gold)",
    },
    {
      key: "current_assets",
      label: "Ativo circulante",
      type: "bar",
      values: records.map((row) => row.currentAssets),
      color: "var(--color-ember-400)",
    },
    {
      key: "noncurrent_assets",
      label: "Ativo não circulante",
      type: "bar",
      values: records.map((row) => row.noncurrentAssets),
      color: "var(--color-gem-azure)",
    },
    {
      key: "current_liabilities",
      outlined: true,
      label: "Passivo circulante",
      type: "bar",
      values: records.map((row) => row.currentLiabilities),
      color: "var(--color-gem-jade)",
    },
    {
      key: "noncurrent_liabilities",
      outlined: true,
      label: "Passivo não circulante",
      type: "bar",
      values: records.map((row) => row.noncurrentLiabilities),
      color: "var(--color-pastel-rose)",
    },
  ];
  return series.filter((item) => item.values.some((value) => value !== null));
}

/**
 * True when every period reports the figure as inapplicable to the filer's
 * regime — a deliberate n/d, not a gap of ours.
 *
 * Read from the API's own `null_reasons` (ADR 0008). A chart of six empty slots
 * says "we could not compute this", which is the opposite of what the domain is
 * actually stating.
 */
function inapplicable(periods: Analysis[], key: IndicatorKey): boolean {
  return periods.every((p) => p.indicators.null_reasons[key] === "inapplicable_regime");
}

/** A series without data shows a single dash instead of an empty axis. */
function EmptySeries() {
  return (
    <div
      className="flex flex-col items-center justify-center gap-1 text-center"
      style={{ height: 170 }}
    >
      <span className="nums text-2xl text-copy-600">{DASH}</span>
    </div>
  );
}
