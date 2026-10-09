"use client";

import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from "@headlessui/react";
import { Fragment, useState } from "react";
import { FiBarChart2, FiChevronDown, FiFileText, FiInfo } from "react-icons/fi";
import { IndicatorChart } from "@/components/IndicatorChart";
import { DASH, money, toNum, yearOf } from "@/lib/format";
import type { Analysis, Decimalish, IncomeStatementSummary } from "@/lib/types";

type HistoryRange = "5" | "10" | "max";
type ValueMode = "simple" | "full";
type IncomeKey = keyof IncomeStatementSummary;

interface IncomePeriod {
  reference_date: string;
  income_statement?: IncomeStatementSummary | null;
}

interface IncomeRow {
  id: string;
  label: string;
  key: IncomeKey;
}

const INCOME_ROWS: IncomeRow[] = [
  { id: "revenue", label: "Receita líquida", key: "revenue" },
  { id: "costs", label: "Custos", key: "costs" },
  { id: "gross-profit", label: "Lucro bruto", key: "gross_profit" },
  { id: "operating-expenses", label: "Despesas/Receitas operacionais", key: "operating_expenses" },
  { id: "ebitda", label: "EBITDA", key: "ebitda" },
  { id: "dep-amort", label: "Depreciação/Amortização", key: "dep_amort" },
  { id: "ebit", label: "EBIT", key: "ebit" },
  { id: "income-tax", label: "Impostos sobre o lucro", key: "income_tax_expense" },
  { id: "net-income", label: "Lucro líquido consolidado", key: "net_income_total" },
];

const FULL_AMOUNT_FORMAT = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

export function IncomeStatementTable({
  history,
  ttm,
}: {
  history: IncomePeriod[];
  ttm: IncomePeriod | null;
}) {
  const [historyRange, setHistoryRange] = useState<HistoryRange>("5");
  const [valueMode, setValueMode] = useState<ValueMode>("simple");
  const [activeChart, setActiveChart] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  const selectedHistory =
    historyRange === "max" ? history : history.slice(-Number(historyRange));
  const periods: { period: IncomePeriod; label: string; live: boolean }[] = [
    ...(ttm?.income_statement
      ? [{ period: ttm, label: "ÚLT. 12M", live: true }]
      : []),
    ...[...selectedHistory].reverse().map((period) => ({
      period,
      label: yearOf(period.reference_date),
      live: false,
    })),
  ];
  const activeRow = INCOME_ROWS.find((row) => row.id === activeChart) ?? null;
  const chartPeriods = [
    ...selectedHistory.map((period) => ({ period, label: yearOf(period.reference_date) })),
    ...(ttm?.income_statement ? [{ period: ttm, label: "ÚLT. 12M" }] : []),
  ];
  const chartHasData = Boolean(
    activeRow &&
      chartPeriods.some((item) =>
        toNum(item.period.income_statement?.[activeRow.key]) !== null,
      ),
  );

  function formatAmount(value: Decimalish | undefined): string {
    const amount = toNum(value);
    if (amount === null) return DASH;
    if (valueMode === "full") return FULL_AMOUNT_FORMAT.format(amount);
    return money(amount).replace("R$ ", "");
  }

  return (
    <div className="panel flex flex-col gap-4 p-4 sm:p-5">
      <header className="flex flex-wrap items-center justify-between gap-x-5 gap-y-3 pb-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <div className="relative flex items-center gap-2 after:absolute after:inset-x-0 after:-bottom-2.5 after:h-0.5 after:bg-accent-500">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-canvas-800 text-accent-300">
              <FiFileText aria-hidden size={16} />
            </span>
            <h3
              id="ticker-income-statement-heading"
              className="card-title"
            >
              Demonstração de resultados
            </h3>
          </div>
          <button
            type="button"
            aria-expanded={helpOpen}
            aria-controls="income-statement-help"
            onClick={() => setHelpOpen((open) => !open)}
            className="inline-flex items-center gap-1.5 text-xs text-copy-300 transition-colors hover:text-copy-50 focus-visible:outline-1 focus-visible:outline-accent-400"
          >
            <FiInfo aria-hidden size={14} />
            Entenda nossos dados
          </button>
        </div>

        <div
          className="inline-flex items-center border-b border-vault-700"
          role="group"
          aria-label="Período da demonstração de resultados"
        >
          {([
            ["5", "5 anos"],
            ["10", "10 anos"],
            ["max", "Máx"],
          ] as const).map(([range, label]) => {
            const selected = historyRange === range;
            return (
              <button
                key={range}
                type="button"
                aria-pressed={selected}
                onClick={() => setHistoryRange(range)}
                className={`border-b-2 px-3 py-1.5 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-400 ${
                  selected
                    ? "border-accent-400 text-copy-50"
                    : "border-transparent text-copy-500 hover:text-copy-200"
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </header>

      <div
        id="income-statement-help"
        hidden={!helpOpen}
        role="note"
        className="border-l-2 border-accent-400 bg-copy-200/5 px-3 py-2 text-xs leading-relaxed text-copy-300"
      >
        Os valores vêm das demonstrações consolidadas da CVM e são apresentados em reais. “Últ. 12M”
        soma o período móvel mais recente; os demais valores correspondem a exercícios fechados. Custos,
        despesas e impostos mantêm o sinal informado no demonstrativo. Traços indicam valores ausentes.
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Listbox value={valueMode} onChange={setValueMode}>
          <div className="relative">
            <ListboxButton className="group/value inline-flex items-center gap-2 rounded-md border border-copy-200/20 px-3 py-2 text-xs font-medium text-copy-200 transition-colors hover:bg-copy-200/5 focus-visible:outline-1 focus-visible:outline-accent-400">
              {valueMode === "simple" ? "Valores simples" : "Valores completos"}
              <FiChevronDown
                aria-hidden
                size={14}
                className="text-copy-500 transition-transform group-data-open/value:rotate-180"
              />
            </ListboxButton>
            <ListboxOptions
              anchor="bottom start"
              className="panel z-50 w-44 overflow-y-auto py-1 shadow-xl focus:outline-none [--anchor-gap:0.5rem] [--anchor-max-height:12rem]"
            >
              {([
                ["simple", "Valores simples"],
                ["full", "Valores completos"],
              ] as const).map(([value, label]) => (
                <ListboxOption
                  key={value}
                  value={value}
                  className="cursor-pointer px-3 py-2 text-xs text-copy-200 transition-colors data-focus:bg-canvas-800"
                >
                  {label}
                </ListboxOption>
              ))}
            </ListboxOptions>
          </div>
        </Listbox>
      </div>

      <div className="overflow-x-auto rounded-md border border-copy-200/10 bg-canvas-900" tabIndex={0}>
        <table className="w-full min-w-[820px] border-collapse text-sm">
          <caption className="sr-only">Demonstração de resultados histórica em reais</caption>
          <thead>
            <tr className="border-b border-copy-200/10">
              <th scope="col" className="w-[310px] px-3 py-3 text-left text-xs font-medium text-copy-600 sm:px-4">
                <span className="sr-only">Conta</span>
              </th>
              <th scope="col" className="w-10 px-1 py-3">
                <span className="sr-only">Gráfico histórico</span>
              </th>
              {periods.map(({ period, label, live }) => (
                <th
                  key={`${period.reference_date}:${live ? "ttm" : "annual"}`}
                  scope="col"
                  className="nums min-w-[112px] px-3 py-3 text-center text-xs font-semibold text-copy-50"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {periods.length === 0 ? (
              <tr>
                <td colSpan={2} className="px-4 py-8 text-center text-sm text-copy-500">
                  Não há períodos disponíveis para esta demonstração.
                </td>
              </tr>
            ) : (
              INCOME_ROWS.map((row) => {
                const chartOpen = activeChart === row.id;
                return (
                  <Fragment key={row.id}>
                    <tr className="border-b border-copy-200/10 last:border-0">
                      <th scope="row" className="px-3 py-3 text-left font-medium text-copy-100 sm:px-4">
                        {row.label} - (R$)
                      </th>
                      <td className="px-1 py-2 text-center">
                        <button
                          type="button"
                          aria-label={`${chartOpen ? "Ocultar" : "Ver"} gráfico de ${row.label}`}
                          aria-expanded={chartOpen}
                          aria-controls={chartOpen ? `income-chart-${row.id}` : undefined}
                          onClick={() => setActiveChart(chartOpen ? null : row.id)}
                          className={`pressable inline-flex h-7 w-7 items-center justify-center rounded-md focus-visible:outline-1 focus-visible:outline-accent-400 ${
                            chartOpen
                              ? "bg-accent-500/15 text-accent-300"
                              : "text-copy-500 hover:bg-canvas-800 hover:text-copy-200"
                          }`}
                        >
                          <FiBarChart2 aria-hidden size={16} />
                        </button>
                      </td>
                      {periods.map(({ period, live }) => {
                        const value = period.income_statement?.[row.key];
                        const missing = toNum(value) === null;
                        return (
                          <td
                            key={`${period.reference_date}:${live ? "ttm" : "annual"}`}
                            className={`nums px-3 py-3 text-center font-semibold ${
                              missing ? "text-copy-600" : "text-copy-50"
                            }`}
                          >
                            {formatAmount(value)}
                          </td>
                        );
                      })}
                    </tr>
                    {chartOpen && (
                      <tr id={`income-chart-${row.id}`} className="border-b border-copy-200/10 bg-canvas-850/50">
                        <td colSpan={2 + periods.length} className="px-4 py-4">
                          <div className="mb-2 text-xs font-medium text-copy-400">
                            Evolução de {row.label.toLocaleLowerCase()}
                          </div>
                          {chartHasData ? (
                            <IndicatorChart
                              key={`${row.id}:${historyRange}`}
                              labels={chartPeriods.map((item) => item.label)}
                              values={chartPeriods.map((item) =>
                                toNum(item.period.income_statement?.[row.key]),
                              )}
                              color="var(--color-accent-400)"
                              formatKind="money"
                              mode="bars"
                              ghostLast={Boolean(ttm?.income_statement)}
                              average={null}
                              height={176}
                            />
                          ) : (
                            <p className="py-5 text-center text-xs text-copy-500">
                              Não há valores disponíveis para esta série no período selecionado.
                            </p>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
