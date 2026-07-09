import { YearBars } from "@/components/YearBars";
import { money, pct, price, toNum, yearOf } from "@/lib/format";
import { sectorColor } from "@/lib/sectors";
import type { Analysis, IndicatorKey } from "@/lib/types";

/** Compact money for a bar's top label — drops the "R$ " to save width. */
const barMoney = (n: number) => money(n).replace("R$ ", "");

type ChartSpec = {
  key: IndicatorKey;
  label: string;
  hint: string;
  format: (n: number) => string;
};

const CHARTS: ChartSpec[] = [
  { key: "revenue", label: "Receita", hint: "Receita líquida do exercício", format: barMoney },
  { key: "net_income", label: "Lucro líquido", hint: "Lucro atribuído aos controladores", format: barMoney },
  { key: "dividends", label: "Dividendos", hint: "Proventos pagos no exercício", format: barMoney },
  { key: "eps", label: "LPA", hint: "Lucro por ação", format: (n) => price(n) },
  { key: "fcf", label: "Fluxo de caixa livre", hint: "Caixa operacional − CAPEX", format: barMoney },
  { key: "roe", label: "ROE", hint: "Retorno sobre o patrimônio", format: (n) => pct(n) },
];

/** Per-year bar charts of the headline figures over the closed-year history. */
export function HistoryCharts({ history, sector }: { history: Analysis[]; sector: string }) {
  const color = sectorColor(sector);
  const labels = history.map((h) => yearOf(h.reference_date));

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {CHARTS.map((c) => {
        const values = history.map((h) => toNum(h.indicators[c.key]));
        return (
          <div key={c.key} className="panel flex flex-col gap-2 p-5" title={c.hint}>
            <span className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              {c.label}
            </span>
            <YearBars labels={labels} values={values} color={color} format={c.format} />
          </div>
        );
      })}
    </div>
  );
}
