import { IndicatorChart } from "@/components/IndicatorChart";
import { DASH, LAST_12M_SHORT, toNum, yearOf } from "@/lib/format";
import { BASIS_HINT, BASIS_LABEL, valueFormatter } from "@/lib/indicators";
import type { FormatKind } from "@/lib/indicators";
import { reasonCopy } from "@/lib/null-reasons";
import { sectorColor } from "@/lib/sectors";
import type { Analysis, IndicatorKey, NullReason } from "@/lib/types";

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
};

/**
 * The charts, grouped by the statement they come from rather than by the figure
 * that happened to be available.
 *
 * The page used to carry six per-figure bar charts that had accreted one at a
 * time, and **net debt appeared in none of them** — the figure that decides
 * whether a good year is durable (#142). Grouping by statement pairs the filed
 * figures that are read together.
 *
 * Ratios are deliberately absent here: they have a drill-down of their own with
 * a scale, the asset's own average and the min/max (#31/#34). What belongs on
 * this section is the statement itself, in reais.
 */
type ChartGroup = { title: string; charts: ChartSpec[] };

const GROUPS: ChartGroup[] = [
  {
    title: "Resultado",
    charts: [
      {
        key: "net_income",
        label: "Receita e lucro líquido",
        hint: "Receita líquida do exercício, com o lucro dos controladores dentro dela",
        kind: "money",
        totalKey: "net_income_total",
        envelope: { key: "revenue", label: "Receita" },
        seriesLabel: "Lucro líquido",
      },
      {
        key: "distributions_per_security",
        label: "Proventos por papel",
        hint: "Direitos de caixa B3 com data ex no exercício",
        kind: "price",
      },
      {
        key: "fcf",
        label: "Fluxo de caixa livre",
        hint: "Caixa operacional − CAPEX",
        kind: "money",
      },
    ],
  },
  {
    title: "Balanço e alavancagem",
    charts: [
      {
        key: "total_liabilities",
        label: "Ativo e passivo",
        hint: "Ativo total, com o passivo com terceiros dentro dele — o vão é o patrimônio",
        kind: "money",
        envelope: { key: "total_assets", label: "Ativo total" },
        seriesLabel: "Passivo",
      },
      {
        key: "net_debt",
        label: "Dívida líquida",
        hint: "Dívida total − caixa e equivalentes CPC 03",
        kind: "money",
      },
      {
        key: "net_debt_to_ebitda",
        label: "Dív. líquida / EBITDA",
        hint: "Anos de EBITDA para quitar a dívida líquida",
        kind: "multiple",
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
  const periods = ttm ? [...history, ttm] : history;

  const groups = GROUPS.map((group) => ({
    ...group,
    // A whole section the filer's regime makes meaningless must not render — the
    // rule #33 set for the indicator groups. Leverage for a bank is the case
    // this exists for: deposits are its raw material, not borrowing. The API
    // says so itself (`null_reasons`), so nothing here restates the guard.
    charts: group.charts.filter((c) => !inapplicable(periods, c.key)),
  })).filter((group) => group.charts.length > 0);

  return (
    <div className="flex flex-col gap-10">
      {groups.map((group) => (
        <div key={group.title} className="flex flex-col gap-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
            {group.title}
          </h3>
          <div className="grid gap-4 lg:grid-cols-3">
            {group.charts.map((c) => {
              const values = periods.map((p) => toNum(p.indicators[c.key]));
              const envelope = c.envelope
                ? {
                    values: periods.map((p) => toNum(p.indicators[c.envelope!.key])),
                    label: c.envelope.label,
                  }
                : null;
              // The bars chart the controllers' slice; the consolidated total is
              // named beside it for the latest period, and only when it reads
              // differently there (ADR 0026).
              const latest = periods[periods.length - 1];
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
                  {values.every((v) => v === null) ? (
                    <EmptySeries reason={firstReason(periods, c.key)} />
                  ) : (
                    <IndicatorChart
                      labels={labels}
                      values={values}
                      color={color}
                      formatKind={c.kind}
                      ghostLast={ttm !== null}
                      mode="bars"
                      average={null}
                      height={170}
                      envelope={envelope}
                      seriesLabel={c.seriesLabel}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
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

/** The first recorded cause across the series — they agree in practice. */
function firstReason(periods: Analysis[], key: IndicatorKey): NullReason | undefined {
  for (const p of periods) {
    const reason = p.indicators.null_reasons[key];
    if (reason) return reason;
  }
  return undefined;
}

/**
 * A series with nothing in it says why, instead of drawing an empty axis.
 *
 * A chart card with grid lines, tick labels and no bars reads as a rendering
 * failure. It is also the one place the app would be flattering itself: an empty
 * frame hides that the gap is *ours* (WEGE3 files no dividend line in the DFC
 * our mapper reads, so the paid basis is `source_account_absent` while the
 * declared one is complete). The grid cells already name their nulls; so does
 * this.
 *
 * A cause that is a gap of ours is coloured as the warning it is, exactly as in
 * the grid — a deliberate n/d stays quiet.
 */
function EmptySeries({ reason }: { reason: NullReason | undefined }) {
  const copy = reasonCopy(reason);
  return (
    <div
      className="flex flex-col items-center justify-center gap-1 text-center"
      style={{ height: 170 }}
    >
      <span className="nums text-2xl text-ink-700">{DASH}</span>
      <span
        className={`text-[0.68rem] ${copy.intentional ? "text-ink-600" : "text-ember-400"}`}
      >
        {copy.short}
      </span>
      <span className="max-w-[26ch] text-[0.6rem] leading-snug text-ink-700">
        {copy.long}
      </span>
    </div>
  );
}
