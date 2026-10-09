"use client";

import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from "@headlessui/react";
import { Fragment, useMemo, useState } from "react";
import { FiBarChart2, FiChevronDown, FiInfo, FiTrendingUp } from "react-icons/fi";
import { IndicatorChart } from "@/components/IndicatorChart";
import { DASH, money, toNum, yearOf } from "@/lib/format";
import type { Analysis, Decimalish, SourceAccountRef } from "@/lib/types";

type HistoryRange = "5" | "10" | "max";
type ValueMode = "simple" | "full";

interface CashFlowPeriod {
  reference_date: string;
  cash_flow_statement?: SourceAccountRef[];
  indicators: Analysis["indicators"];
}

interface CashFlowNode {
  code: string;
  name: string;
  values: Map<string, Decimalish>;
  children: CashFlowNode[];
  sort: number;
}

interface VisibleCashFlowNode {
  node: CashFlowNode;
  level: number;
}

const FULL_AMOUNT_FORMAT = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

function buildCashFlowTree(periods: CashFlowPeriod[]): CashFlowNode[] {
  const nodes = new Map<string, CashFlowNode>();
  periods.forEach((period, periodIndex) => {
    (period.cash_flow_statement ?? []).forEach((account, accountIndex) => {
      if (!/^6(?:\.|$)/.test(account.code)) return;
      const node = nodes.get(account.code) ?? {
        code: account.code,
        name: account.name,
        values: new Map<string, Decimalish>(),
        children: [],
        sort: 0,
      };
      node.name = account.name || node.name || `Conta ${account.code}`;
      node.values.set(period.reference_date, account.value);
      node.sort = periodIndex * 10000 + accountIndex;
      nodes.set(account.code, node);
    });
  });

  const roots: CashFlowNode[] = [];
  for (const node of nodes.values()) {
    const parentCode = node.code.slice(0, node.code.lastIndexOf("."));
    const parent = nodes.get(parentCode);
    if (parent) parent.children.push(node);
    else roots.push(node);
  }

  const sortTree = (items: CashFlowNode[]) => {
    items.sort((left, right) => left.sort - right.sort);
    items.forEach((item) => sortTree(item.children));
  };
  sortTree(roots);
  return roots;
}

function flattenVisible(
  roots: CashFlowNode[],
  expanded: Set<string>,
): VisibleCashFlowNode[] {
  const rows: VisibleCashFlowNode[] = [];
  const visit = (nodes: CashFlowNode[], level: number) => {
    for (const node of nodes) {
      rows.push({ node, level });
      if (node.children.length > 0 && expanded.has(node.code)) {
        visit(node.children, level + 1);
      }
    }
  };
  visit(roots, 0);
  return rows;
}

function expandableCodes(nodes: CashFlowNode[]): string[] {
  return nodes.flatMap((node) => [
    ...(node.children.length > 0 ? [node.code] : []),
    ...expandableCodes(node.children),
  ]);
}

function allCodes(nodes: CashFlowNode[]): string[] {
  return nodes.flatMap((node) => [node.code, ...allCodes(node.children)]);
}

export function CashFlowStatementTable({ history }: { history: CashFlowPeriod[] }) {
  const [historyRange, setHistoryRange] = useState<HistoryRange>("5");
  const [valueMode, setValueMode] = useState<ValueMode>("simple");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(["6.01", "6.05"]),
  );
  const [activeChart, setActiveChart] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  const selectedHistory = useMemo(
    () => (historyRange === "max" ? history : history.slice(-Number(historyRange))),
    [history, historyRange],
  );
  const periods = [...selectedHistory].reverse();
  const roots = useMemo(() => buildCashFlowTree(selectedHistory), [selectedHistory]);
  const visibleRows = flattenVisible(roots, expandedGroups);
  const allExpandable = expandableCodes(roots);
  const allExpanded = allExpandable.every((code) => expandedGroups.has(code));
  const noneExpanded = allExpandable.every((code) => !expandedGroups.has(code));

  function setAllGroups(expand: boolean) {
    setExpandedGroups(expand ? new Set(allExpandable) : new Set());
    if (!expand) setActiveChart(null);
  }

  function toggleGroup(node: CashFlowNode) {
    const collapsing = expandedGroups.has(node.code);
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(node.code)) next.delete(node.code);
      else next.add(node.code);
      return next;
    });
    if (collapsing && activeChart && activeChart !== node.code) {
      const descendants = new Set(allCodes(node.children));
      if (descendants.has(activeChart)) setActiveChart(null);
    }
  }

  function formatAmount(value: Decimalish | undefined): string {
    const amount = toNum(value);
    if (amount === null) return DASH;
    if (valueMode === "full") return FULL_AMOUNT_FORMAT.format(amount);
    return money(amount).replace("R$ ", "");
  }

  const activeNode = visibleRows.find(({ node }) => node.code === activeChart)?.node ?? null;
  const chartHasData = activeChart === "fcf"
    ? selectedHistory.some((period) => toNum(period.indicators.fcf) !== null)
    : activeNode !== null && selectedHistory.some((period) =>
        toNum(activeNode.values.get(period.reference_date)) !== null,
      );

  return (
    <div className="panel flex flex-col gap-4 p-4 sm:p-5">
      <header className="flex flex-wrap items-center justify-between gap-x-5 gap-y-3 pb-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <div className="relative flex items-center gap-2 after:absolute after:inset-x-0 after:-bottom-2.5 after:h-0.5 after:bg-accent-500">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-canvas-800 text-accent-300">
              <FiTrendingUp aria-hidden size={16} />
            </span>
            <h3
              id="ticker-cash-flow-heading"
              className="card-title"
            >
              Fluxo de caixa
            </h3>
          </div>
          <button
            type="button"
            aria-expanded={helpOpen}
            aria-controls="cash-flow-help"
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
          aria-label="Período do fluxo de caixa"
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
        id="cash-flow-help"
        hidden={!helpOpen}
        role="note"
        className="border-l-2 border-accent-400 bg-copy-200/5 px-3 py-2 text-xs leading-relaxed text-copy-300"
      >
        As linhas reproduzem as contas do fluxo de caixa consolidado da CVM, com valores em reais e
        a mesma hierarquia do demonstrativo. O fluxo de caixa livre usa a definição da análise: caixa
        operacional menos aquisições de imobilizado e intangível. Traços indicam valor não informado.
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
        <table className="w-full min-w-[860px] border-collapse text-sm">
          <caption className="sr-only">Fluxo de caixa histórico em reais, conforme a CVM</caption>
          <thead>
            <tr className="border-b border-copy-200/10">
              <th scope="col" className="w-[340px] px-3 py-3 text-left text-xs font-medium text-copy-600 sm:px-4">
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
            {periods.length === 0 || roots.length === 0 ? (
              <tr>
                <td colSpan={2 + periods.length} className="px-4 py-8 text-center text-sm text-copy-500">
                  Não há demonstrativos de fluxo de caixa disponíveis.
                </td>
              </tr>
            ) : (
              <>
                {visibleRows.map(({ node, level }) => {
                  const hasChildren = node.children.length > 0;
                  const expanded = expandedGroups.has(node.code);
                  const chartOpen = activeChart === node.code;
                  return (
                    <Fragment key={node.code}>
                      <tr className="border-b border-copy-200/10">
                        <th
                          scope="row"
                          className={`px-3 py-3 text-left sm:px-4 ${
                            level === 0
                              ? "font-semibold text-accent-300"
                              : "font-medium text-copy-100"
                          }`}
                        >
                          <span className="flex items-center gap-2" style={{ paddingLeft: `${level * 1.15}rem` }}>
                            {hasChildren ? (
                              <button
                                type="button"
                                aria-expanded={expanded}
                                aria-label={`${expanded ? "Recolher" : "Expandir"} ${node.name}`}
                                onClick={() => toggleGroup(node)}
                                className="inline-flex shrink-0 focus-visible:outline-1 focus-visible:outline-accent-400"
                              >
                                <FiChevronDown
                                  aria-hidden
                                  size={14}
                                  className={`transition-transform ${expanded ? "rotate-180" : ""}`}
                                />
                              </button>
                            ) : (
                              <span aria-hidden className="w-[14px] shrink-0" />
                            )}
                            <span>{node.name || `Conta ${node.code}`} - (R$)</span>
                          </span>
                        </th>
                        <td className="px-1 py-2 text-center">
                          <button
                            type="button"
                            aria-label={`${chartOpen ? "Ocultar" : "Ver"} gráfico de ${node.name}`}
                            aria-expanded={chartOpen}
                            aria-controls={chartOpen ? `cash-flow-chart-${node.code}` : undefined}
                            onClick={() => setActiveChart(chartOpen ? null : node.code)}
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
                          const value = node.values.get(period.reference_date);
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
                        <tr id={`cash-flow-chart-${node.code}`} className="border-b border-copy-200/10 bg-canvas-850/50">
                          <td colSpan={2 + periods.length} className="px-4 py-4">
                            <div className="mb-2 text-xs font-medium text-copy-400">
                              Evolução de {node.name.toLocaleLowerCase()} · períodos fechados
                            </div>
                            {chartHasData ? (
                              <IndicatorChart
                                key={`${node.code}:${historyRange}`}
                                labels={selectedHistory.map((period) => yearOf(period.reference_date))}
                                values={selectedHistory.map((period) =>
                                  toNum(node.values.get(period.reference_date)),
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
                })}
                <tr className="border-b border-copy-200/10 last:border-0">
                  <th scope="row" className="px-3 py-3 text-left font-semibold text-accent-300 sm:px-4">
                    <span className="flex items-center gap-2 pl-0">
                      <span className="w-[14px] shrink-0" aria-hidden />
                      Fluxo de caixa livre - (R$)
                    </span>
                  </th>
                  <td className="px-1 py-2 text-center">
                    <button
                      type="button"
                      aria-label={`${activeChart === "fcf" ? "Ocultar" : "Ver"} gráfico de fluxo de caixa livre`}
                      aria-expanded={activeChart === "fcf"}
                      aria-controls={activeChart === "fcf" ? "cash-flow-chart-fcf" : undefined}
                      onClick={() => setActiveChart(activeChart === "fcf" ? null : "fcf")}
                      className={`pressable inline-flex h-7 w-7 items-center justify-center rounded-md focus-visible:outline-1 focus-visible:outline-accent-400 ${
                        activeChart === "fcf"
                          ? "bg-accent-500/15 text-accent-300"
                          : "text-copy-500 hover:bg-canvas-800 hover:text-copy-200"
                      }`}
                    >
                      <FiBarChart2 aria-hidden size={16} />
                    </button>
                  </td>
                  {periods.map((period) => {
                    const value = period.indicators.fcf;
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
                {activeChart === "fcf" && (
                  <tr id="cash-flow-chart-fcf" className="border-b border-copy-200/10 bg-canvas-850/50">
                    <td colSpan={2 + periods.length} className="px-4 py-4">
                      <div className="mb-2 text-xs font-medium text-copy-400">
                        Evolução de fluxo de caixa livre · períodos fechados
                      </div>
                      {chartHasData ? (
                        <IndicatorChart
                          key={`fcf:${historyRange}`}
                          labels={selectedHistory.map((period) => yearOf(period.reference_date))}
                          values={selectedHistory.map((period) => toNum(period.indicators.fcf))}
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
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
