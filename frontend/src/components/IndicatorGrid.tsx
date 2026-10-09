"use client";

/**
 * Current indicator cards and their period-by-period history for one ticker.
 *
 * Client-side because each cell exposes two affordances — an evolution chart and
 * the reference doc — that open a shared modal. The per-indicator series is built
 * from the full closed-year history plus the current rolling window, so both
 * views open the same drill-down for a given indicator.
 */
import {
  Dialog,
  DialogPanel,
  DialogTitle,
  Tab,
  TabGroup,
  TabList,
  TabPanel,
  TabPanels,
} from "@headlessui/react";
import { useState } from "react";
import type { ReactNode } from "react";
import {
  FiBarChart2,
  FiChevronDown,
  FiFilter,
  FiGrid,
  FiInfo,
  FiSearch,
  FiTrendingUp,
  FiX,
} from "react-icons/fi";
import { IndicatorDetail } from "@/components/IndicatorDetail";
import type { IndicatorSeries } from "@/components/IndicatorDetail";
import { toNum, yearOf } from "@/lib/format";
import { indicatorDoc } from "@/lib/indicator-docs";
import {
  INDICATOR_GROUPS,
  INDICATORS,
  HISTORY_ONLY_INDICATORS,
  groupColor,
  specByKey,
  specsByGroup,
} from "@/lib/indicators";
import type { IndicatorGroup, IndicatorSpec } from "@/lib/indicators";
import type { Analysis, IndicatorContract, IndicatorKey, Indicators } from "@/lib/types";

type HistoryRange = "5" | "10" | "max";

const HISTORY_ROW_PRIORITY: IndicatorKey[] = [
  "dividend_yield",
  "pe_basic",
  "pb",
  "eps_basic",
  "bvps",
  "ev_ebitda",
  "ev_ebit",
  "price_to_ebitda",
  "price_to_ebit",
  "psr",
  "price_to_cfo",
  "price_to_fcf",
  "ev_revenue",
  "ev_cfo",
  "ev_fcf",
  "earnings_yield",
  "enterprise_value",
  "market_cap",
];

const HISTORY_INDICATORS = [...INDICATORS, ...HISTORY_ONLY_INDICATORS];

const HISTORY_GROUP_OVERRIDES: Partial<Record<IndicatorKey, IndicatorGroup>> = {
  payout_cash_paid_in_period: "Rentabilidade",
  asset_turnover: "Rentabilidade",
};

const HISTORY_GROUP_TITLES: Record<IndicatorGroup, string> = {
  Valuation: "Indicadores de valuation",
  Endividamento: "Indicadores de endividamento",
  Eficiência: "Indicadores de eficiência",
  Rentabilidade: "Indicadores de rentabilidade",
  Crescimento: "Indicadores de crescimento",
};

const HISTORY_FILTER_LABELS: Partial<Record<IndicatorKey, string>> = {
  dividend_yield: "Dividend yield",
  psr: "P/Receita (PSR)",
  net_debt_to_ebitda: "Dív. líq./EBITDA",
  net_debt_to_equity: "Dív. líquida/PL",
  current_ratio: "Liq. corrente",
  liabilities_to_assets: "Passivos/Ativos",
  equity_to_assets: "PL/Ativos",
  quick_ratio: "Liq. seca",
  cash_ratio: "Liq. imediata",
  asset_turnover: "Giro do ativo",
  revenue_cagr_5y: "CAGR receita (5A)",
  ebitda_cagr_5y: "CAGR EBITDA (5A)",
  ebit_cagr_5y: "CAGR EBIT (5A)",
  net_income_cagr_5y: "CAGR Lucro Lq. (5A)",
};

function historyFilterLabel(spec: IndicatorSpec): string {
  return HISTORY_FILTER_LABELS[spec.key] ?? spec.label;
}

export function IndicatorGrid({
  indicators,
  indicatorContract,
  compare,
  compareLabel,
  history,
  ttm,
  isCurrent,
}: {
  indicators: Indicators;
  indicatorContract?: Partial<Record<IndicatorKey, IndicatorContract>>;
  /** The closed exercise each cell is measured against — null on that exercise itself. */
  compare: Indicators | null;
  /** That exercise's year, named once in the header rather than in 29 cells. */
  compareLabel: string | null;
  history: Analysis[];
  ttm: Analysis | null;
  isCurrent: boolean;
}) {
  const [openKey, setOpenKey] = useState<IndicatorKey | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [historyRange, setHistoryRange] = useState<HistoryRange>("5");
  const [selectedHistoryKeys, setSelectedHistoryKeys] = useState<Set<IndicatorKey>>(
    () => new Set(HISTORY_INDICATORS.map(({ key }) => key)),
  );

  const seriesFor = (key: IndicatorKey): IndicatorSeries => {
    if (key === "tag_along") {
      return { labels: ["Atual"], values: [toNum(indicators[key])], ghostLast: false };
    }
    const labels = history.map((h) => yearOf(h.reference_date));
    const values = history.map((h) => toNum(h.indicators[key]));
    if (ttm) {
      labels.push("Últ. 12M");
      values.push(toNum(ttm.indicators[key]));
    }
    return { labels, values, ghostLast: ttm !== null };
  };

  const openSpec = openKey ? specByKey(openKey) : undefined;

  const visibleGroups = INDICATOR_GROUPS.flatMap((group) => {
    const specs = specsByGroup(group).filter((s) => isCurrent || s.key !== "tag_along");
    // Hide a section only when the API marks every cell as inapplicable.
    // Individual null cells retain the cause supplied by the API.
    const groupInapplicable = specs.every(
      (s) =>
        toNum(indicators[s.key]) === null &&
        indicators.null_reasons?.[s.key] === "inapplicable_regime",
    );
    if (groupInapplicable) return [];
    return [{ group, specs, accent: groupColor(group) }];
  });
  const allCollapsed =
    visibleGroups.length > 0 && visibleGroups.every(({ group }) => collapsedGroups.has(group));
  const historicalPeriods = ttm ? [...history, ttm] : history;
  const historySpecs = HISTORY_INDICATORS.map((spec) => ({
    ...spec,
    group: HISTORY_GROUP_OVERRIDES[spec.key] ?? spec.group,
  }))
    .filter(
      (spec) =>
        historicalPeriods.length === 0 ||
        !historicalPeriods.every(
          (period) =>
            toNum(period.indicators[spec.key]) === null &&
            period.indicators.null_reasons?.[spec.key] === "inapplicable_regime",
        ),
    )
    .sort((left, right) => {
      const leftPriority = HISTORY_ROW_PRIORITY.indexOf(left.key);
      const rightPriority = HISTORY_ROW_PRIORITY.indexOf(right.key);
      const leftOrder =
        leftPriority < 0
          ? HISTORY_ROW_PRIORITY.length +
            HISTORY_INDICATORS.findIndex(({ key }) => key === left.key)
          : leftPriority;
      const rightOrder =
        rightPriority < 0
          ? HISTORY_ROW_PRIORITY.length +
            HISTORY_INDICATORS.findIndex(({ key }) => key === right.key)
          : rightPriority;
      return leftOrder - rightOrder;
    });
  const selectedHistorySpecs = historySpecs.filter((spec) => selectedHistoryKeys.has(spec.key));

  function toggleGroup(group: string) {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  }

  function toggleAllGroups() {
    setCollapsedGroups(
      allCollapsed ? new Set() : new Set(visibleGroups.map(({ group }) => group)),
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <TabGroup>
        {({ selectedIndex }) => (
          <>
            <div className="flex items-center justify-between gap-3 border-b border-copy-200/10">
              <TabList className="flex min-w-0 flex-1 gap-4 overflow-x-auto">
                <Tab className="outline-none focus-visible:rounded-sm focus-visible:ring-1 focus-visible:ring-accent-400">
                  {({ selected }) => (
                    <span
                      className={`flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-2 pb-2.5 pt-1 text-[0.7rem] font-semibold uppercase tracking-wide ${
                        selected ? "border-up text-up" : "border-transparent text-copy-500 hover:text-copy-200"
                      }`}
                    >
                      <FiGrid aria-hidden size={15} />
                      Indicadores
                    </span>
                  )}
                </Tab>
                <Tab className="outline-none focus-visible:rounded-sm focus-visible:ring-1 focus-visible:ring-accent-400">
                  {({ selected }) => (
                    <span
                      className={`flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-2 pb-2.5 pt-1 text-[0.7rem] font-semibold uppercase tracking-wide ${
                        selected ? "border-up text-up" : "border-transparent text-copy-500 hover:text-copy-200"
                      }`}
                    >
                      <FiBarChart2 aria-hidden size={15} />
                      Histórico de indicadores
                    </span>
                  )}
                </Tab>
              </TabList>
              {selectedIndex === 0 && (
                <button
                  type="button"
                  onClick={toggleAllGroups}
                  className="pressable shrink-0 whitespace-nowrap rounded-md px-2.5 py-1.5 text-[0.68rem] font-semibold text-accent-300 hover:bg-copy-200/5 hover:text-accent-200 focus-visible:outline-1 focus-visible:outline-accent-400"
                >
                  {allCollapsed ? "Expandir tudo" : "Recolher tudo"}
                </button>
              )}
            </div>

            <TabPanels className="mt-5">
              <TabPanel className="flex flex-col gap-6 focus:outline-none">
                {visibleGroups.map(({ group, specs, accent }) => {
                  const collapsed = collapsedGroups.has(group);
                  return (
                    <section key={group} className="flex flex-col gap-3">
                      <button
                        type="button"
                        aria-expanded={!collapsed}
                        onClick={() => toggleGroup(group)}
                        className="pressable relative flex w-full items-center justify-between gap-4 rounded-md border border-copy-200/10 border-l-4 px-4 py-3 text-left hover:bg-copy-200/5 sm:px-5"
                        style={{
                          borderLeftColor: accent,
                          backgroundColor: `color-mix(in oklab, ${accent} 10%, var(--color-canvas-850))`,
                        }}
                      >
                        <span className="text-sm font-semibold" style={{ color: accent }}>
                          {groupHeading(group)}
                        </span>
                        <span className="flex shrink-0 items-center gap-3">
                          <FiChevronDown
                            aria-hidden
                            size={17}
                            className={`text-copy-300 transition-transform ${collapsed ? "" : "rotate-180"}`}
                          />
                        </span>
                      </button>
                      {!collapsed && (
                        <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
                          {specs.map((spec) => (
                            <IndicatorCell
                              key={spec.key}
                              spec={spec}
                              indicators={indicators}
                              onOpen={() => setOpenKey(spec.key)}
                            />
                          ))}
                        </div>
                      )}
                    </section>
                  );
                })}
              </TabPanel>

              <TabPanel className="flex flex-col gap-4 focus:outline-none">
                <HistoryToolbar
                  range={historyRange}
                  onRangeChange={setHistoryRange}
                  specs={historySpecs}
                  selectedKeys={selectedHistoryKeys}
                  onApplyFilter={setSelectedHistoryKeys}
                />
                <IndicatorHistoryTable
                  specs={selectedHistorySpecs}
                  history={history}
                  ttm={ttm}
                  range={historyRange}
                  onOpen={(key) => setOpenKey(key)}
                />
              </TabPanel>
            </TabPanels>
          </>
        )}
      </TabGroup>

      {openKey && openSpec && (
        <IndicatorDetail
          spec={openSpec}
          doc={indicatorDoc(openKey)}
          series={seriesFor(openKey)}
          accent={groupColor(openSpec.group)}
          isCurrent={isCurrent}
          contract={indicatorContract?.[openKey]}
          previous={openKey === "tag_along" ? null : compare?.[openKey] ?? null}
          previousLabel={compareLabel}
          onSelectKey={setOpenKey}
          onClose={() => setOpenKey(null)}
        />
      )}
    </div>
  );
}

function HistoryToolbar({
  range,
  onRangeChange,
  specs,
  selectedKeys,
  onApplyFilter,
}: {
  range: HistoryRange;
  onRangeChange: (range: HistoryRange) => void;
  specs: IndicatorSpec[];
  selectedKeys: Set<IndicatorKey>;
  onApplyFilter: (keys: Set<IndicatorKey>) => void;
}) {
  const [filterOpen, setFilterOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [draftKeys, setDraftKeys] = useState<Set<IndicatorKey>>(() => new Set(selectedKeys));
  const normalizedSearch = search.trim().toLocaleLowerCase("pt-BR");
  const visibleSpecs = specs.filter((spec) => {
    const searchableText = [
      historyFilterLabel(spec),
      spec.label,
      spec.hint,
      spec.group,
      HISTORY_GROUP_TITLES[spec.group],
    ]
      .join(" ")
      .toLocaleLowerCase("pt-BR");
    return searchableText.includes(normalizedSearch);
  });
  const visibleGroups = INDICATOR_GROUPS.flatMap((group) => {
    const groupSpecs = visibleSpecs.filter((spec) => spec.group === group);
    return groupSpecs.length > 0 ? [{ group, specs: groupSpecs }] : [];
  });

  function openFilter() {
    setDraftKeys(new Set(selectedKeys));
    setSearch("");
    setFilterOpen(true);
  }

  function cancelFilter() {
    setDraftKeys(new Set(selectedKeys));
    setSearch("");
    setFilterOpen(false);
  }

  function applyFilter() {
    onApplyFilter(new Set(draftKeys));
    setFilterOpen(false);
  }

  function toggleKey(key: IndicatorKey) {
    setDraftKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function selectVisible() {
    setDraftKeys((current) => new Set([...current, ...visibleSpecs.map(({ key }) => key)]));
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={openFilter}
          className="pressable flex items-center gap-2 rounded-md border border-copy-200/20 px-3 py-2 text-xs font-semibold text-copy-100 hover:border-copy-200/40 focus-visible:outline-2 focus-visible:outline-accent-400"
        >
          <FiFilter aria-hidden size={14} />
          Filtrar indicadores
        </button>

        <div
          className="inline-flex items-center border-b border-vault-700"
          role="group"
          aria-label="Período do histórico de indicadores"
        >
          {([
            ["5", "5 anos"],
            ["10", "10 anos"],
            ["max", "Máx"],
          ] as const).map(([value, label]) => {
            const selected = range === value;
            return (
              <button
                key={value}
                type="button"
                aria-pressed={selected}
                onClick={() => onRangeChange(value)}
                className={`border-b-2 px-3 py-1.5 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-up ${
                  selected
                    ? "border-up text-up"
                    : "border-transparent text-copy-500 hover:text-copy-200"
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      <Dialog open={filterOpen} onClose={cancelFilter} className="relative z-50">
        <div className="modal-backdrop fixed inset-0 bg-canvas-950/85" aria-hidden />
        <div className="fixed inset-0 flex items-center justify-center overflow-y-auto p-3 sm:p-6">
          <DialogPanel className="grid max-h-[92vh] w-full max-w-3xl grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden rounded-lg border border-copy-200/20 bg-canvas-950 shadow-2xl">
            <header className="border-b border-copy-200/10 px-5 pb-4 pt-5 sm:px-6">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <DialogTitle className="text-sm font-semibold text-copy-100">
                    Filtrar indicadores
                  </DialogTitle>
                  <p className="mt-1 text-xs text-copy-500">
                    Selecione os indicadores que deseja visualizar no histórico.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={cancelFilter}
                  aria-label="Fechar filtro"
                  className="pressable -mr-1 -mt-1 rounded-md p-1.5 text-copy-500 hover:bg-copy-200/5 hover:text-copy-100 focus-visible:outline-2 focus-visible:outline-accent-400"
                >
                  <FiX aria-hidden size={17} />
                </button>
              </div>

              <label htmlFor="indicator-history-search" className="sr-only">
                Pesquisar indicadores
              </label>
              <div className="relative mt-4">
                <FiSearch
                  aria-hidden
                  size={15}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-copy-500"
                />
                <input
                  id="indicator-history-search"
                  type="search"
                  autoComplete="off"
                  value={search}
                  onChange={(event) => setSearch(event.currentTarget.value)}
                  placeholder="Pesquisar indicadores"
                  className="w-full rounded-md border border-copy-200/15 bg-canvas-900 py-2.5 pl-9 pr-3 text-sm text-copy-100 placeholder:text-copy-600 focus:border-accent-400 focus:outline-none"
                />
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={selectVisible}
                  disabled={visibleSpecs.length === 0}
                  className="pressable rounded-md border border-copy-200/30 px-3 py-2 text-xs font-semibold text-copy-100 hover:border-copy-200/60 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent-400"
                >
                  Selecionar todos
                </button>
                <button
                  type="button"
                  onClick={() => setDraftKeys(new Set())}
                  className="pressable rounded-md border border-copy-200/30 px-3 py-2 text-xs font-semibold text-copy-100 hover:border-copy-200/60 focus-visible:outline-2 focus-visible:outline-accent-400"
                >
                  Limpar
                </button>
              </div>
            </header>

            <div className="min-h-0 space-y-3 overflow-y-auto px-5 py-4 sm:px-6">
              {visibleGroups.length === 0 ? (
                <p
                  className="rounded-md border border-copy-200/10 bg-canvas-900 px-4 py-6 text-center text-sm text-copy-500"
                  role="status"
                >
                  Nenhum indicador encontrado.
                </p>
              ) : (
                visibleGroups.map(({ group, specs: groupSpecs }) => (
                  <section
                    key={group}
                    className="rounded-lg border border-copy-200/20 bg-canvas-900 px-3 py-3.5 sm:px-4"
                  >
                    <h3 className="mb-3 text-xs font-semibold text-copy-200">
                      {HISTORY_GROUP_TITLES[group]}
                    </h3>
                    <div className="grid grid-cols-1 gap-x-5 gap-y-2.5 sm:grid-cols-2">
                      {groupSpecs.map((spec) => (
                        <label
                          key={spec.key}
                          className="flex min-h-6 cursor-pointer items-center gap-2 text-xs text-copy-200 hover:text-copy-50"
                        >
                          <input
                            type="checkbox"
                            checked={draftKeys.has(spec.key)}
                            onChange={() => toggleKey(spec.key)}
                            className="size-4 shrink-0 accent-up focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-up"
                          />
                          <span>{historyFilterLabel(spec)}</span>
                        </label>
                      ))}
                    </div>
                  </section>
                ))
              )}
            </div>

            <footer className="flex justify-end gap-2 border-t border-copy-200/10 px-5 py-4 sm:px-6">
              <button
                type="button"
                onClick={cancelFilter}
                className="pressable rounded-md border border-copy-200/30 px-3.5 py-2 text-xs font-semibold text-copy-100 hover:border-copy-200/60 focus-visible:outline-2 focus-visible:outline-accent-400"
              >
                Cancelar
              </button>
              <button
                type="button"
                onClick={applyFilter}
                className="pressable rounded-md bg-up px-3.5 py-2 text-xs font-semibold text-canvas-950 hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-up"
              >
                Aplicar filtro
              </button>
            </footer>
          </DialogPanel>
        </div>
      </Dialog>
    </>
  );
}

function IndicatorHistoryTable({
  specs,
  history,
  ttm,
  range,
  onOpen,
}: {
  specs: IndicatorSpec[];
  history: Analysis[];
  ttm: Analysis | null;
  range: HistoryRange;
  onOpen: (key: IndicatorKey) => void;
}) {
  // The selected range counts the live 12-month column when it exists.
  const closedCount =
    range === "max" ? history.length : Math.max(Number(range) - (ttm ? 1 : 0), 0);
  const visibleHistory =
    range === "max"
      ? history
      : closedCount === 0
        ? []
        : history.slice(-closedCount);
  const periods = [
    ...(ttm ? [{ analysis: ttm, label: "Últ. 12M", id: "ttm" }] : []),
    ...[...visibleHistory].reverse().map((analysis) => ({
      analysis,
      label: yearOf(analysis.reference_date),
      id: analysis.reference_date,
    })),
  ];

  if (periods.length === 0) {
    return (
      <p className="rounded-lg border border-copy-200/10 bg-canvas-950 p-5 text-sm text-copy-500" role="status">
        Ainda não há períodos disponíveis para este ticker.
      </p>
    );
  }

  if (specs.length === 0) {
    return (
      <p className="rounded-lg border border-copy-200/10 bg-canvas-950 p-5 text-sm text-copy-500" role="status">
        Nenhum indicador selecionado. Use o filtro para adicioná-los ao histórico.
      </p>
    );
  }

  return (
    <div
      className="overflow-x-auto rounded-lg border border-copy-200/10 bg-canvas-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-up"
      tabIndex={0}
      role="region"
      aria-label="Histórico de indicadores por período"
      aria-roledescription="tabela rolável"
    >
      <table className="w-full min-w-max border-collapse text-sm">
        <thead>
          <tr className="border-b border-copy-200/10">
            <th className="sticky left-0 z-10 w-[180px] min-w-[180px] bg-canvas-900 px-3 py-3 text-left text-xs font-semibold text-copy-100 sm:w-[260px] sm:min-w-[260px] sm:px-4">
              Indicador
            </th>
            {periods.map((period) => (
              <th
                key={period.id}
                className={`min-w-[128px] px-4 py-3 text-right text-xs font-semibold text-copy-100 ${
                  period.id === "ttm" ? "border-l border-copy-200/10" : ""
                }`}
              >
                {period.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {specs.map((spec) => (
            <tr
              key={spec.key}
              className="group border-b border-copy-200/10 last:border-0 transition-colors hover:bg-canvas-850"
            >
              <th className="sticky left-0 z-10 bg-canvas-900 px-3 py-2 text-left font-medium text-copy-200 group-hover:bg-canvas-850 sm:px-4">
                <span className="flex w-[156px] flex-wrap items-center gap-1 sm:w-[228px] sm:flex-nowrap sm:gap-1.5">
                  <span className="min-w-0 basis-full break-words text-xs sm:flex-1 sm:basis-auto sm:truncate sm:text-sm">{spec.label}</span>
                  <CellButton label={`Sobre ${spec.label}`} onClick={() => onOpen(spec.key)}>
                    <FiInfo size={14} />
                  </CellButton>
                  <CellButton label={`Gráfico de ${spec.label}`} onClick={() => onOpen(spec.key)}>
                    <FiTrendingUp size={15} />
                  </CellButton>
                </span>
              </th>
              {periods.map(({ analysis, id }) => {
                const raw = analysis.indicators[spec.key];
                const missing = toNum(raw) === null;
                return (
                  <td
                    key={`${spec.key}:${id}`}
                    className={`nums px-4 py-2 text-right font-medium ${
                      id === "ttm" ? "border-l border-copy-200/10" : ""
                    } ${
                      missing ? "text-copy-600" : "text-copy-100"
                    }`}
                  >
                    {spec.format(raw)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function groupHeading(group: string): string {
  return group;
}

function IndicatorCell({
  spec,
  indicators,
  onOpen,
}: {
  spec: IndicatorSpec;
  indicators: Indicators;
  onOpen: () => void;
}) {
  const raw = indicators[spec.key];
  const text = spec.format(raw);
  const missing = toNum(raw) === null;

  // Neutral ink, always. The sign is already in the glyph; colouring it too made
  // the growth cells read as alerts among thirty neutral ones — see the note in
  // `lib/indicators.ts`.
  const valueColor = missing
    ? "var(--color-copy-600)"
    : "var(--color-copy-50)";

  return (
    <div
      className="panel panel-hover group relative p-3 sm:p-3.5"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-1.5">
          <div className="card-title min-w-0">
            {spec.label}
          </div>
          <CellButton label={`Sobre ${spec.label}`} onClick={onOpen}>
            <FiInfo size={14} />
          </CellButton>
        </div>
        {spec.key !== "tag_along" ? (
          <CellButton label={`Evolução de ${spec.label}`} onClick={onOpen}>
            <FiBarChart2 size={16} />
          </CellButton>
        ) : null}
      </div>
      <div className="nums mt-2 text-base font-semibold leading-tight" style={{ color: valueColor }}>
        {text}
      </div>
    </div>
  );
}

function CellButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="pressable inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-copy-500 hover:bg-canvas-800 hover:text-copy-200 focus-visible:outline-1 focus-visible:outline-accent-500 sm:h-8 sm:w-8"
    >
      {children}
    </button>
  );
}
