"use client";

/**
 * Modal drill-down for a single indicator: its evolution across the closed-year
 * history (plus the current rolling window as a trailing ghost point) and the reference doc —
 * formula as computed, what it measures, and where it carries meaning across the
 * B3 subsectors.
 *
 * One modal serves both indicator grids on the page: the series and the doc are
 * properties of the indicator, not of the view it was clicked from. The title is
 * a picker, so comparing P/L against P/VP costs one click instead of a round
 * trip through the grid.
 */
import { Dialog, DialogPanel, Tab, TabGroup, TabList, TabPanel, TabPanels } from "@headlessui/react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { FiAlertTriangle, FiBarChart2, FiTrendingUp, FiX } from "react-icons/fi";
import { IndicatorChart } from "@/components/IndicatorChart";
import type { ChartMode } from "@/components/IndicatorChart";
import { IndicatorPicker } from "@/components/IndicatorPicker";
import { DASH, toNum } from "@/lib/format";
import type { IndicatorDoc, RelevanceNote } from "@/lib/indicator-docs";
import {
  BASIS_HINT,
  BASIS_LABEL,
  INDICATORS,
  basisOf,
  basisPair,
  deltaText,
  formatKindOf,
  valueFormatter,
} from "@/lib/indicators";
import type { Basis, IndicatorSpec } from "@/lib/indicators";
import { reasonCopy } from "@/lib/null-reasons";
import { sectorMeta } from "@/lib/sectors";
import type { Decimalish, IndicatorContract, IndicatorKey, NullReason } from "@/lib/types";

export interface IndicatorSeries {
  labels: string[];
  values: (number | null)[];
  /** The trailing point is the current rolling window, not a closed exercise. */
  ghostLast: boolean;
}

export function IndicatorDetail({
  spec,
  doc,
  series,
  accent,
  sector,
  contract,
  nullReason,
  previous,
  previousLabel,
  onSelectKey,
  onClose,
}: {
  spec: IndicatorSpec;
  doc: IndicatorDoc;
  series: IndicatorSeries;
  accent: string;
  sector: string;
  contract?: IndicatorContract;
  /** Set when this indicator is null in the view the reader came from. */
  nullReason: NullReason | undefined;
  /** Same indicator on the latest closed exercise, for the change tile. */
  previous: Decimalish;
  previousLabel: string | null;
  onSelectKey: (key: IndicatorKey) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<ChartMode>("bars");

  // `Dialog` owns the portal, the focus trap, the scroll lock, the Esc key and
  // returning focus to whatever opened it — all of which were hand-written here
  // before. What stays ours is ←/→ walking the indicator list, so scanning a
  // group does not mean reopening the picker for each one.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      // While the picker's list is open the arrows are its own.
      if (document.querySelector('[role="listbox"]')) return;
      // A `_total` column is not in the grid's list; walk from its controllers'
      // sibling so the arrows keep working while reading the consolidated basis.
      const gridKey = basisPair(spec.key)?.controllers ?? spec.key;
      const at = INDICATORS.findIndex((s) => s.key === gridKey);
      if (at < 0) return;
      const step = e.key === "ArrowRight" ? 1 : -1;
      const next = (at + step + INDICATORS.length) % INDICATORS.length;
      e.preventDefault();
      onSelectKey(INDICATORS[next].key);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onSelectKey, spec.key]);

  const formatKind = formatKindOf(spec);
  const fmt = valueFormatter(formatKind);
  const fmtOrDash = (n: number | null) => (n === null ? DASH : fmt(n));

  const plottable = series.values.filter((v) => v !== null).length;
  const reason = nullReason ? reasonCopy(nullReason) : null;
  // Switching basis is switching indicator: `roe` and `roe_total` are separate
  // columns with their own series and their own doc (ADR 0026), so the toggle
  // reuses the same path the picker takes.
  const pair = basisPair(spec.key);

  // The reference statistics describe the closed exercises only. The current window
  // overlaps the last one and is not a comparable period, so averaging it in
  // would weight the most recent months twice.
  const closed = series.values
    .slice(0, series.ghostLast ? -1 : undefined)
    .filter((v): v is number => v !== null);
  const average = closed.length ? closed.reduce((a, b) => a + b, 0) / closed.length : null;
  const current = series.values[series.values.length - 1] ?? null;
  const currentLabel = series.labels[series.labels.length - 1];

  // The headline value is neutral like its cell: the chart below it is where
  // direction is coloured, and colouring both said the same thing twice.
  const heroColor = current === null ? "var(--color-ink-600)" : "var(--color-ink-50)";

  // The change against the closed exercise. In a cell this was a bare arrow with
  // nothing naming the other side; here both ends fit ("7,5% → 5,4%").
  const from = toNum(previous);
  const change =
    current !== null && from !== null && previousLabel
      ? (() => {
          const text = deltaText(formatKind, current, from);
          return text ? { text, from, to: current } : null;
        })()
      : null;

  // `Dialog` portals into <body> on its own, which is what this modal needs:
  // `position: fixed` anchors to the nearest *transformed* ancestor rather than
  // to the window, and the ticker page has two (`.rise`, whose `forwards` fill
  // keeps a transform applied, and `.panel-hover` on the card). Anchored in
  // place, the backdrop inherited the card's box and pushed the panel's top out
  // of reach with nothing left to scroll.
  return (
    <Dialog open onClose={onClose} className="relative z-50">
      <div
        className="modal-backdrop fixed inset-0 flex items-start justify-center overflow-y-auto bg-canvas-950/85 p-4 sm:p-8"
        aria-hidden
      />
      {/* On a landscape screen the tall single column ran past the viewport, so
          past ~1024px the panel turns into a rectangle: chart on one side, the
          reference doc on the other, each scrolling on its own. */}
      <div className="fixed inset-0 flex items-start justify-center overflow-y-auto p-4 sm:p-8">
      <DialogPanel
        // `grid-rows-[minmax(0,1fr)]`: without it the row is sized by its
        // content, overshoots the panel's max height and gets clipped by
        // `overflow-hidden` — with no scrollbar anywhere, which is the bug this
        // whole modal had. Pinning the row makes the columns scroll instead.
        className="modal-panel relative my-auto w-full max-w-3xl rounded-lg border border-copy-200/10 bg-canvas-900 p-6 sm:p-7 lg:grid lg:max-h-[88vh] lg:max-w-6xl lg:grid-cols-2 lg:grid-rows-[minmax(0,1fr)] lg:gap-8 lg:overflow-hidden lg:p-8"
        aria-label={spec.label}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Fechar"
          className="pressable absolute right-5 top-5 z-10 rounded-lg border border-copy-200/10 bg-canvas-850 p-2 text-copy-500 hover:border-accent-400/40 hover:text-copy-200 sm:right-6 sm:top-6"
        >
          <FiX size={16} />
        </button>

        {/* ------------------------------------------------ chart column --- */}
        <div className="lg:min-h-0 lg:overflow-y-auto lg:pr-1">
        <header className="flex items-start justify-between gap-4 pr-12">
          <div className="flex items-start gap-3">
            <span className="mt-1.5 h-8 w-[3px] rounded-full" style={{ backgroundColor: accent }} />
            <div>
              {/* Lists grid indicators only, so while reading a `_total` column
                  it shows that column's controllers' sibling — the basis toggle
                  below is what states which slice is on screen. */}
              <IndicatorPicker
                value={pair?.controllers ?? spec.key}
                label={spec.label}
                onChange={onSelectKey}
              />
              <p className="mt-0.5 text-xs" style={{ color: accent }}>
                {spec.group}
              </p>
              {pair && (
                <BasisToggle
                  basis={basisOf(spec.key)}
                  onChange={(b) => onSelectKey(b === "total" ? pair.total : pair.controllers)}
                />
              )}
            </div>
          </div>
        </header>

        {reason && (
          <div className="mt-5 flex gap-3 rounded-lg border border-warning/25 bg-warning/5 p-3.5">
            <FiAlertTriangle className="mt-0.5 shrink-0 text-warning" size={15} />
            <p className="text-xs leading-relaxed text-copy-400">
              <span className="text-copy-200">
                {reason.intentional ? "Sem valor de propósito" : "Sem valor por falta de dado"} (
                {sectorMeta(sector).label.toLowerCase()}):
              </span>{" "}
              {reason.long}
              {reason.intentional && <> Veja &ldquo;Onde engana&rdquo;.</>}
            </p>
          </div>
        )}

        {/* ------------------------------------------------- the reading --- */}
        {/* The value leads, because it is what the reader came for; the rest is
            reference, and reference does not deserve a card each — four of them
            competed with the number they exist to qualify. */}
        <div className="mt-5">
          <div className="flex flex-wrap items-baseline gap-x-3">
            {/* Direction is coloured only where the sign means something: a
                growth of −2,3% is bad news, a P/L of 8,24× is neither. Same
                rule the grid cell applies. */}
            <span className="nums text-4xl font-semibold leading-none" style={{ color: heroColor }}>
              {fmtOrDash(current)}
            </span>
            {change && (
              <span
                className="nums text-sm"
                style={{ color: change.to >= change.from ? "var(--color-up)" : "var(--color-down)" }}
              >
                {change.text}
              </span>
            )}
          </div>
          <p className="mt-1.5 text-xs text-copy-500">
            {currentLabel}
            {change && (
              <>
                {" · "}vs exercício {previousLabel}:{" "}
                <span className="nums text-copy-400">
                  {fmt(change.from)} → {fmt(change.to)}
                </span>
              </>
            )}
          </p>
        </div>

        {/* -------------------------------------------------------- chart --- */}
        <section className="mt-6">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h4 className="text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-copy-500">
              Evolução
            </h4>
            {plottable >= 2 && <ModeToggle mode={mode} onChange={setMode} accent={accent} />}
          </div>

          {plottable >= 2 ? (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-[0.68rem] text-copy-500">
                <span
                  className="flex items-center gap-1.5"
                  title="Média aritmética dos anos fechados — o período atual fica de fora, por não ser um período comparável."
                >
                  {average !== null && (
                    <span
                      className="h-0 w-3.5 shrink-0"
                      style={{ borderTop: `2px dashed ${accent}`, opacity: 0.7 }}
                    />
                  )}
                  média dos exercícios
                  <span className="nums text-copy-300">{fmtOrDash(average)}</span>
                </span>
                <span
                  className="flex items-center gap-1.5"
                  title="Menor e maior valor entre os exercícios fechados."
                >
                  mín · máx
                  <span className="nums text-copy-300">
                    {closed.length
                      ? `${fmt(Math.min(...closed))} · ${fmt(Math.max(...closed))}`
                      : DASH}
                  </span>
                </span>
              </div>

              <div className="rounded-lg border border-copy-200/10 bg-canvas-950 px-2 pb-2 pt-3">
                <IndicatorChart
                  labels={series.labels}
                  values={series.values}
                  ghostLast={series.ghostLast}
                  color={accent}
                  formatKind={formatKind}
                  mode={mode}
                  average={average}
                />
              </div>
              {series.ghostLast && (
                <p className="mt-2 text-[0.68rem] text-copy-600">
                  O traço tracejado representa o período atual — uma janela móvel, não um
                  ano fechado.
                </p>
              )}
            </>
          ) : (
            <p className="rounded-lg border border-copy-200/10 bg-canvas-950 p-4 text-xs text-copy-600">
              Série insuficiente: são necessários ao menos dois períodos com valor apurado.
            </p>
          )}
        </section>
        </div>

        <div className="hairline my-6 lg:hidden" />

        {/* -------------------------------------------------- doc column --- */}
        {/* Two tabs, split by what the reader is asking: "what does this number
            mean" against "how did you arrive at it". The second is the audit
            trail — formula as computed and its declared basis — and it was
            crowding the first. A third ("Comparar",
            #137) lands here when there are enough companies to compare against. */}
        <TabGroup as="section" className="flex flex-col lg:min-h-0 lg:overflow-hidden">
          <TabList className="mb-4 flex gap-1 pr-12">
            {["Entenda", "Como calculamos"].map((title) => (
              <Tab
                key={title}
                className="rounded-md px-3 py-1 text-[0.68rem] transition-colors focus-visible:outline-1 focus-visible:outline-accent-500 data-selected:bg-canvas-800"
              >
                {/* Colour comes from the render prop, not a `data-selected:`
                    utility: it collides with the base text colour and loses on
                    stylesheet order, so the label stayed dim when selected. */}
                {({ selected, hover }) => (
                  <span
                    style={{
                      color: selected
                        ? "var(--color-copy-50)"
                        : hover
                          ? "var(--color-copy-300)"
                          : "var(--color-copy-600)",
                    }}
                  >
                    {title}
                  </span>
                )}
              </Tab>
            ))}
          </TabList>

          <TabPanels className="lg:min-h-0 lg:overflow-y-auto lg:pr-2">
            <TabPanel className="flex flex-col gap-6 focus:outline-none">
              <div>
                <h4 className="mb-2 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-copy-500">
                  Para que serve
                </h4>
                <p className="text-sm leading-relaxed text-copy-200">{doc.what}</p>
              </div>

              <NoteList
                title="Onde é mais relevante"
                notes={doc.strongIn}
                markerColor="var(--color-up)"
              />
              <NoteList
                title="Onde engana"
                notes={doc.weakIn}
                markerColor="var(--color-down)"
                hollow
              />
            </TabPanel>

            <TabPanel className="flex flex-col gap-6 focus:outline-none">
              <div>
                <h4 className="mb-2 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-copy-500">
                  Fórmula
                </h4>
                <p className="nums rounded-lg border border-accent-400/20 bg-accent-400/5 px-3.5 py-2.5 text-sm text-accent-300">
                  {doc.formula}
                </p>
              </div>

              {doc.caveat && (
                <div>
                  <h4 className="mb-2 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-copy-500">
                    Como calculamos
                  </h4>
                  <p className="text-xs leading-relaxed text-copy-400">{doc.caveat}</p>
                </div>
              )}

              {contract && <ContractSummary contract={contract} />}
            </TabPanel>
          </TabPanels>
        </TabGroup>
      </DialogPanel>
      </div>
    </Dialog>
  );
}

/**
 * Which statement slice the modal is reading (ADR 0026).
 *
 * Always visible for an indicator that has two, even when they happen to agree:
 * the reader has to know a choice exists — "17,3%" and "17,7%" are both correct
 * answers to different questions.
 */
function BasisToggle({
  basis,
  onChange,
}: {
  basis: Basis;
  onChange: (basis: Basis) => void;
}) {
  const item = (value: Basis) => {
    const on = basis === value;
    return (
      <button
        type="button"
        onClick={() => onChange(value)}
        aria-pressed={on}
        title={BASIS_HINT[value]}
        className={`rounded-full px-2 py-0.5 transition-colors ${
          on ? "bg-canvas-800 text-copy-200" : "text-copy-600 hover:text-copy-400"
        } focus-visible:outline-1 focus-visible:outline-accent-500`}
      >
        {BASIS_LABEL[value]}
      </button>
    );
  };
  return (
    <div className="mt-2 flex items-center gap-1 rounded-md border border-copy-200/10 p-0.5 text-[0.62rem]">
      {item("controllers")}
      {item("total")}
    </div>
  );
}

function ModeToggle({
  mode,
  onChange,
  accent,
}: {
  mode: ChartMode;
  onChange: (mode: ChartMode) => void;
  accent: string;
}) {
  const item = (value: ChartMode, label: string, icon: ReactNode) => {
    const on = mode === value;
    return (
      <button
        type="button"
        aria-label={label}
        aria-pressed={on}
        onClick={() => onChange(value)}
        className="rounded-md px-2 py-1 transition-colors focus-visible:outline-1 focus-visible:outline-accent-500"
        style={{
          backgroundColor: on ? "var(--color-canvas-800)" : "transparent",
          color: on ? accent : "var(--color-copy-600)",
        }}
      >
        {icon}
      </button>
    );
  };
  return (
    <div className="flex gap-0.5 rounded-lg border border-copy-200/10 p-0.5">
      {item("bars", "Ver em barras", <FiBarChart2 size={14} />)}
      {item("line", "Ver em linha", <FiTrendingUp size={14} />)}
    </div>
  );
}

function NoteList({
  title,
  notes,
  markerColor,
  hollow = false,
}: {
  title: string;
  notes: RelevanceNote[];
  markerColor: string;
  hollow?: boolean;
}) {
  return (
    <div>
      <h4 className="mb-2.5 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-copy-500">
        {title}
      </h4>
      <ul className="flex flex-col gap-2.5">
        {notes.map((n) => (
          <li key={n.where} className="flex gap-3">
            <span
              className="mt-[0.42rem] h-1.5 w-1.5 shrink-0 rounded-full"
              style={
                hollow
                  ? { border: `1px solid ${markerColor}` }
                  : { backgroundColor: markerColor }
              }
            />
            <p className="text-sm leading-relaxed text-copy-400">
              <span className="text-copy-100">{n.where}</span>
              <span className="text-copy-600"> — </span>
              {n.why}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ContractSummary({ contract }: { contract: IndicatorContract }) {
  const labels: Record<string, string> = {
    operating_cash_flow: "fluxo de caixa operacional",
    current_assets_less_inventories: "ativo circulante menos estoques",
    current_liabilities: "passivo circulante",
    cash_equivalents: "caixa e equivalentes de caixa",
    filed_total_free_float_percent: "percentual total divulgado de ações em circulação",
    one_hundred: "100",
    gross_acquisition_capex: "aquisições de imobilizado e intangível",
    company_closing: "companhia no fechamento",
    company_period_cash: "fluxos de caixa da companhia no período",
    company_filed_distribution: "distribuição de capital divulgada pela companhia",
    latest_filed_assembly_on_or_before_reference_date: "última assembleia divulgada até a data de referência",
    market_capitalization: "valor de mercado da companhia",
    company_enterprise_value: "valor da empresa",
    company_market_convention: "companhia em base de mercado",
    market_capitalization_plus_net_debt_plus_nci: "valor de mercado + dívida líquida + participação de minoritários",
    cash_and_cash_equivalents: "caixa e equivalentes de caixa",
    revenue: "receita líquida",
    attributable_net_income: "lucro líquido dos controladores",
    view_period: "período da visão analisada",
    not_applicable: "não aplicável",
    enterprise_value: "valor da empresa",
    free_cash_flow: "fluxo de caixa livre",
    annualized_operating_cash_flow: "fluxo de caixa operacional anualizado",
    annualized_free_cash_flow: "fluxo de caixa livre anualizado",
    annualized_revenue: "receita líquida anualizada",
    annualized_ebitda: "EBITDA anualizado",
    net_income: "lucro líquido",
    listed_classes_outstanding: "ações em circulação das classes listadas",
    analysis: "visão analisada",
    analysis_price_basis: "base de preço da análise",
    analysis_share_count_basis: "base de ações da análise",
    annualized_attributable_net_income: "lucro atribuível anualizado",
    closing_attributable_bvps: "valor patrimonial por ação no fechamento",
    closing_outstanding_shares: "ações em circulação no fechamento",
    selected_basic_eps: "LPA básico",
  selected_weighted_average_class_rights: "média ponderada da classe e direitos",
  security_selected_evidence: "papel individual",
  security_closing_capital: "lucro líquido e quantidade de ações",
  net_income_per_selected_closing_share: "lucro líquido por ação",
  selected_closing_total_unit_equivalent: "total de ações, com ajuste para units",
  cpc41_basic_eps: "lucro por ação divulgado",
  cpc41_weighted_average_class_rights: "média ponderada da classe e direitos",
    last_twelve_months: "últimos 12 meses",
    market_convention_basic_eps: "LPA básico estimado",
    reference_date_closing: "fechamento da data de referência",
    security_closing: "papel individual em base de fechamento",
    security_cpc41: "papel individual com resultado divulgado",
    security_market_convention: "papel individual",
    security_price: "preço do papel",
  };
  const label = (value: string) => labels[value] ?? value.replaceAll("_", " ");

  return (
    <div className="border-t border-copy-200/10 pt-5">
      <h4 className="mb-3 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-copy-500">
        Contrato publicado pela API
      </h4>
      <dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
        <ContractField label="Base" value={label(contract.basis)} />
        <ContractField label="Numerador" value={label(contract.numerator)} />
        <ContractField label="Denominador" value={label(contract.denominator)} />
        <ContractField label="Período" value={label(contract.reference_period)} />
        <ContractField label="Preço" value={label(contract.price_basis)} />
        <ContractField label="Ações" value={label(contract.share_basis)} />
        <ContractField label="Fontes" value={contract.provenance.map(label).join(" + ") || "—"} />
      </dl>
    </div>
  );
}

function ContractField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-copy-600">{label}</dt>
      <dd className="mt-0.5 text-copy-300">{value}</dd>
    </div>
  );
}
