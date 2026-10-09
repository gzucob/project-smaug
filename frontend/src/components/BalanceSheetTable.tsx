"use client";

import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from "@headlessui/react";
import { Fragment, useState } from "react";
import { FiBarChart2, FiChevronDown, FiColumns, FiInfo } from "react-icons/fi";
import { IndicatorChart } from "@/components/IndicatorChart";
import { DASH, money, toNum, yearOf } from "@/lib/format";
import type { Decimalish, Indicators } from "@/lib/types";

type BalanceValueKey =
  | "total_assets"
  | "current_assets"
  | "noncurrent_assets"
  | "current_liabilities"
  | "noncurrent_liabilities"
  | "equity_total";

type BalanceGroupId = "assets" | "liabilities";
type HistoryRange = "5" | "10" | "max";
type ValueMode = "simple" | "full";

export interface BalanceSheetPeriod {
  reference_date: string;
  indicators: Pick<Indicators, BalanceValueKey>;
}

interface BalanceRow {
  id: string;
  label: string;
  key: BalanceValueKey;
}

interface BalanceGroup extends BalanceRow {
  groupId: BalanceGroupId;
  children: BalanceRow[];
}

const BALANCE_GROUPS: BalanceGroup[] = [
  {
    id: "assets-total",
    groupId: "assets",
    label: "Ativo total",
    key: "total_assets",
    children: [
      { id: "current-assets", label: "Ativo circulante", key: "current_assets" },
      { id: "noncurrent-assets", label: "Ativo não circulante", key: "noncurrent_assets" },
    ],
  },
  {
    id: "liabilities-total",
    groupId: "liabilities",
    label: "Passivo total",
    // The passivo + patrimônio total is the balancing side of total assets.
    // Reuse the same persisted balance total instead of calculating one here.
    key: "total_assets",
    children: [
      { id: "current-liabilities", label: "Passivo circulante", key: "current_liabilities" },
      { id: "noncurrent-liabilities", label: "Passivo não circulante", key: "noncurrent_liabilities" },
      { id: "consolidated-equity", label: "Patrimônio líquido consolidado", key: "equity_total" },
    ],
  },
];

const FULL_AMOUNT_FORMAT = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

export function BalanceSheetTable({ history }: { history: BalanceSheetPeriod[] }) {
  const [historyRange, setHistoryRange] = useState<HistoryRange>("5");
  const [valueMode, setValueMode] = useState<ValueMode>("simple");
  const [expandedGroups, setExpandedGroups] = useState<Set<BalanceGroupId>>(
    () => new Set(BALANCE_GROUPS.map((group) => group.groupId)),
  );
  const [activeChart, setActiveChart] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  const selectedHistory =
    historyRange === "max" ? history : history.slice(-Number(historyRange));
  const periods = [...selectedHistory].reverse();
  const allRows = BALANCE_GROUPS.flatMap((group) => [group, ...group.children]);
  const activeRow = allRows.find((row) => row.id === activeChart) ?? null;
  const chartHasData = activeRow && selectedHistory.some(
    (period) => toNum(period.indicators[activeRow.key]) !== null,
  );
  const allExpanded = BALANCE_GROUPS.every((group) => expandedGroups.has(group.groupId));
  const noneExpanded = BALANCE_GROUPS.every((group) => !expandedGroups.has(group.groupId));

  function setAllGroups(expand: boolean) {
    setExpandedGroups(
      expand ? new Set(BALANCE_GROUPS.map((group) => group.groupId)) : new Set(),
    );
    if (!expand) setActiveChart(null);
  }

  function toggleGroup(group: BalanceGroup) {
    const collapsing = expandedGroups.has(group.groupId);
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(group.groupId)) next.delete(group.groupId);
      else next.add(group.groupId);
      return next;
    });
    if (
      collapsing &&
      (activeChart === group.id || group.children.some((row) => row.id === activeChart))
    ) {
      setActiveChart(null);
    }
  }

  function formatAmount(value: Decimalish): string {
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
              <FiColumns aria-hidden size={16} />
            </span>
            <h2
              id="ticker-balance-sheet-heading"
              className="card-title"
            >
              Dados do balanço patrimonial
            </h2>
          </div>
          <button
            type="button"
            aria-expanded={helpOpen}
            aria-controls="balance-sheet-help"
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
          aria-label="Período do balanço patrimonial"
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
        id="balance-sheet-help"
        hidden={!helpOpen}
        role="note"
        className="border-l-2 border-accent-400 bg-copy-200/5 px-3 py-2 text-xs leading-relaxed text-copy-300"
      >
        Os valores são saldos dos exercícios fechados informados nos demonstrativos da CVM. “Valores
        simples” abrevia as grandezas; “Valores completos” mostra os números sem escala. O total de
        passivo e patrimônio corresponde ao ativo total, com os componentes discriminados abaixo. O traço
        indica dado indisponível no período.
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

        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            disabled={allExpanded}
            onClick={() => setAllGroups(true)}
            className="font-semibold text-copy-300 transition-colors hover:text-copy-50 focus-visible:outline-1 focus-visible:outline-accent-400 disabled:cursor-default disabled:text-copy-600"
          >
            Expandir tudo
          </button>
          <span aria-hidden className="text-copy-600">|</span>
          <button
            type="button"
            disabled={noneExpanded}
            onClick={() => setAllGroups(false)}
            className="font-semibold text-copy-300 transition-colors hover:text-copy-50 focus-visible:outline-1 focus-visible:outline-accent-400 disabled:cursor-default disabled:text-copy-600"
          >
            Recolher tudo
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-md border border-copy-200/10 bg-canvas-900" tabIndex={0}>
        <table className="w-full min-w-[760px] border-collapse text-sm">
          <caption className="sr-only">Saldos históricos do balanço patrimonial em reais</caption>
          <thead>
            <tr className="border-b border-copy-200/10">
              <th scope="col" className="w-[310px] px-3 py-3 text-left text-xs font-medium text-copy-600 sm:px-4">
                <span className="sr-only">Conta</span>
              </th>
              <th scope="col" className="w-10 px-1 py-3">
                <span className="sr-only">Gráfico histórico</span>
              </th>
              {periods.map((period) => (
                <th
                  key={period.reference_date}
                  scope="col"
                  className="nums min-w-[112px] px-3 py-3 text-center text-xs font-semibold text-copy-50"
                >
                  {yearOf(period.reference_date)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {history.length === 0 ? (
              <tr>
                <td
                  colSpan={2}
                  className="px-4 py-8 text-center text-sm text-copy-500"
                >
                  Não há exercícios fechados disponíveis para este balanço.
                </td>
              </tr>
            ) : (
              BALANCE_GROUPS.map((group) => {
                const expanded = expandedGroups.has(group.groupId);
                const rows = expanded ? [group, ...group.children] : [group];
                return rows.map((row) => {
                  const isGroup = row.id === group.id;
                  const chartOpen = activeChart === row.id;
                  return (
                    <Fragment key={row.id}>
                      <tr className="border-b border-copy-200/10 last:border-0">
                        <th
                          scope="row"
                          className={`px-3 py-3 text-left sm:px-4 ${
                            isGroup ? "font-semibold text-accent-300" : "font-medium text-copy-100"
                          }`}
                        >
                          {isGroup ? (
                            <button
                              type="button"
                              aria-expanded={expanded}
                              onClick={() => toggleGroup(group)}
                              className="flex w-full items-center gap-2 text-left focus-visible:outline-1 focus-visible:outline-accent-400"
                            >
                              <FiChevronDown
                                aria-hidden
                                size={14}
                                className={`shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
                              />
                              <span>{row.label} - (R$)</span>
                            </button>
                          ) : (
                            <span className="block pl-6">{row.label} - (R$)</span>
                          )}
                        </th>
                        <td className="px-1 py-2 text-center">
                          <button
                            type="button"
                            aria-label={`${chartOpen ? "Ocultar" : "Ver"} gráfico de ${row.label}`}
                            aria-expanded={chartOpen}
                            aria-controls={chartOpen ? `balance-chart-${row.id}` : undefined}
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
                        {periods.map((period) => {
                          const value = period.indicators[row.key];
                          const missing = toNum(value) === null;
                          return (
                            <td
                              key={period.reference_date}
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
                        <tr
                          id={`balance-chart-${row.id}`}
                          className="border-b border-copy-200/10 bg-canvas-850/50"
                        >
                          <td colSpan={2 + periods.length} className="px-4 py-4">
                            <div className="mb-2 text-xs font-medium text-copy-400">
                              Evolução de {row.label.toLocaleLowerCase()} · períodos fechados
                            </div>
                            {chartHasData ? (
                              <IndicatorChart
                                key={`${row.id}:${historyRange}`}
                                labels={selectedHistory.map((period) => yearOf(period.reference_date))}
                                values={selectedHistory.map((period) =>
                                  toNum(period.indicators[row.key]),
                                )}
                                color="var(--color-accent-400)"
                                formatKind="money"
                                mode="bars"
                                ghostLast={false}
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
                });
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
