"use client";

/**
 * Modal drill-down for a single indicator: its evolution across the closed-year
 * history (plus the TTM window as a trailing ghost point) and the reference doc —
 * formula as computed, what it measures, and where it carries meaning across the
 * B3 subsectors.
 *
 * One modal serves both indicator grids on the page: the series and the doc are
 * properties of the indicator, not of the view it was clicked from. The title is
 * a picker, so comparing P/L against P/VP costs one click instead of a round
 * trip through the grid.
 */
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { FiAlertTriangle, FiBarChart2, FiChevronDown, FiTrendingUp, FiX } from "react-icons/fi";
import type { ChartMode } from "@/components/IndicatorChart";
import { DASH, money } from "@/lib/format";
import type { IndicatorDoc, RelevanceNote } from "@/lib/indicator-docs";
import { INDICATOR_GROUPS, specsByGroup } from "@/lib/indicators";
import type { IndicatorSpec } from "@/lib/indicators";
import { sectorMeta } from "@/lib/sectors";
import type { IndicatorKey, SectorKey } from "@/lib/types";

// Recharts is ~115 kB — a third of the ticker page. It is only ever needed once
// the reader opens a drill-down, so it loads with the modal, not with the page.
const IndicatorChart = dynamic(
  () => import("@/components/IndicatorChart").then((m) => m.IndicatorChart),
  {
    ssr: false,
    loading: () => <div className="h-[264px]" aria-hidden />,
  },
);

export interface IndicatorSeries {
  labels: string[];
  values: (number | null)[];
  /** The trailing point is a TTM window, not a closed exercise. */
  ghostLast: boolean;
}

export function IndicatorDetail({
  spec,
  doc,
  series,
  accent,
  sector,
  onSelectKey,
  onClose,
}: {
  spec: IndicatorSpec;
  doc: IndicatorDoc;
  series: IndicatorSeries;
  accent: string;
  sector: string;
  onSelectKey: (key: IndicatorKey) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<ChartMode>("bars");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [onClose]);

  // Axis and tooltip labels drop the "R$ " prefix to save width, as in HistoryCharts.
  const isMoney = spec.format === money;
  const fmt = (n: number) => (isMoney ? spec.format(n).replace("R$ ", "") : spec.format(n));
  const fmtOrDash = (n: number | null) => (n === null ? DASH : fmt(n));

  const plottable = series.values.filter((v) => v !== null).length;
  const notApplicable = doc.naSectors?.includes(sector as SectorKey) ?? false;

  // The reference statistics describe the closed exercises only. The TTM window
  // overlaps the last one and is not a comparable period, so averaging it in
  // would weight the most recent months twice.
  const closed = series.values
    .slice(0, series.ghostLast ? -1 : undefined)
    .filter((v): v is number => v !== null);
  const average = closed.length ? closed.reduce((a, b) => a + b, 0) / closed.length : null;
  const current = series.values[series.values.length - 1] ?? null;
  const currentLabel = series.labels[series.labels.length - 1];

  // Rendered into <body>: `position: fixed` anchors to the nearest *transformed*
  // ancestor rather than to the window, and this modal has two — the `.rise`
  // entrance (which keeps `translateY(0)` under `forwards`) and `.panel-hover`
  // on the card. In place, the backdrop inherited the card's box, took its
  // height instead of the viewport's, and pushed the panel's top out of reach
  // with nothing left to scroll.
  return createPortal(
    <div
      className="modal-backdrop fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-vault-950/85 p-4 sm:p-8"
      onClick={onClose}
      role="presentation"
    >
      {/* On a landscape screen the tall single column ran past the viewport, so
          past ~1024px the panel turns into a rectangle: chart on one side, the
          reference doc on the other, each scrolling on its own. */}
      <div
        // `grid-rows-[minmax(0,1fr)]`: without it the row is sized by its
        // content, overshoots the panel's max height and gets clipped by
        // `overflow-hidden` — with no scrollbar anywhere, which is the bug this
        // whole modal had. Pinning the row makes the columns scroll instead.
        className="modal-panel panel relative my-auto w-full max-w-3xl p-6 sm:p-7 lg:grid lg:max-h-[88vh] lg:max-w-6xl lg:grid-cols-2 lg:grid-rows-[minmax(0,1fr)] lg:gap-8 lg:overflow-hidden lg:p-8"
        role="dialog"
        aria-modal="true"
        aria-label={spec.label}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Fechar"
          className="absolute right-5 top-5 z-10 rounded-lg border border-gold-500/10 bg-vault-900 p-2 text-ink-500 transition-colors hover:border-gold-500/30 hover:text-ink-200 sm:right-6 sm:top-6"
        >
          <FiX size={16} />
        </button>

        {/* ------------------------------------------------ chart column --- */}
        <div className="lg:min-h-0 lg:overflow-y-auto lg:pr-1">
        <header className="flex items-start justify-between gap-4 pr-12">
          <div className="flex items-start gap-3">
            <span className="mt-1.5 h-8 w-[3px] rounded-full" style={{ backgroundColor: accent }} />
            <div>
              {/* The native select sizes itself to its widest option, which would
                  strand the chevron far from the title — so it sits invisible on
                  top of the label and keeps its keyboard and mobile behaviour. */}
              <div className="group/pick relative inline-flex items-center gap-1.5 rounded-md focus-within:outline-1 focus-within:outline-gold-500">
                <h3 className="font-display text-2xl text-ink-50 transition-colors group-hover/pick:text-gold-300">
                  {spec.label}
                </h3>
                <FiChevronDown className="text-ink-500" size={15} />
                <select
                  value={spec.key}
                  onChange={(e) => onSelectKey(e.target.value as IndicatorKey)}
                  aria-label="Trocar de indicador"
                  className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                >
                  {INDICATOR_GROUPS.map((group) => (
                    <optgroup key={group} label={group}>
                      {specsByGroup(group).map((s) => (
                        <option key={s.key} value={s.key}>
                          {s.label}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </div>
              <p className="mt-0.5 text-xs" style={{ color: accent }}>
                {spec.group}
              </p>
            </div>
          </div>
        </header>

        {notApplicable && (
          <div className="mt-5 flex gap-3 rounded-xl border border-gold-500/15 bg-vault-850 p-3.5">
            <FiAlertTriangle className="mt-0.5 shrink-0 text-gold-500" size={15} />
            <p className="text-xs leading-relaxed text-ink-400">
              Não se aplica a {sectorMeta(sector).label.toLowerCase()}. O cálculo retorna{" "}
              <span className="nums">n/d</span> de propósito — veja &ldquo;Onde engana&rdquo;.
            </p>
          </div>
        )}

        {/* -------------------------------------------------------- chart --- */}
        <section className="mt-6">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h4 className="text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-ink-500">
              Evolução
            </h4>
            {plottable >= 2 && <ModeToggle mode={mode} onChange={setMode} accent={accent} />}
          </div>

          {plottable >= 2 ? (
            <>
              <div className="mb-3 grid gap-2 sm:grid-cols-3">
                <Stat
                  label={`Atual · ${currentLabel}`}
                  value={fmtOrDash(current)}
                  color={accent}
                />
                <Stat
                  label="Média dos exercícios"
                  value={fmtOrDash(average)}
                  dashColor={average === null ? undefined : accent}
                  hint="Média aritmética dos exercícios fechados — a janela de 12 meses fica de fora, por não ser um período comparável."
                />
                <Stat
                  label="Mín · Máx"
                  value={
                    closed.length
                      ? `${fmt(Math.min(...closed))} · ${fmt(Math.max(...closed))}`
                      : DASH
                  }
                  hint="Menor e maior valor entre os exercícios fechados."
                />
              </div>

              <div className="rounded-xl border border-gold-500/8 bg-vault-900/40 px-2 pb-2 pt-3">
                <IndicatorChart
                  labels={series.labels}
                  values={series.values}
                  ghostLast={series.ghostLast}
                  color={accent}
                  format={fmt}
                  mode={mode}
                  average={average}
                />
              </div>
              {series.ghostLast && (
                <p className="mt-2 text-[0.68rem] text-ink-600">
                  O traço tracejado são os últimos 12 meses — uma janela móvel, não um
                  exercício fechado.
                </p>
              )}
            </>
          ) : (
            <p className="rounded-xl border border-gold-500/8 bg-vault-900/40 p-4 text-xs text-ink-600">
              Série insuficiente: são necessários ao menos dois períodos com valor apurado.
            </p>
          )}
        </section>
        </div>

        <div className="hairline my-6 lg:hidden" />

        {/* -------------------------------------------------- doc column --- */}
        <section className="flex flex-col gap-6 lg:min-h-0 lg:overflow-y-auto lg:pr-2">
          {/* Clears the close button, which floats over this column on `lg`. */}
          <div className="lg:pr-10">
            <h4 className="mb-2 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-ink-500">
              Fórmula
            </h4>
            <p className="nums rounded-lg border border-gold-500/8 bg-vault-850 px-3.5 py-2.5 text-sm text-gold-300">
              {doc.formula}
            </p>
          </div>

          <div>
            <h4 className="mb-2 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-ink-500">
              Para que serve
            </h4>
            <p className="text-sm leading-relaxed text-ink-200">{doc.what}</p>
          </div>

          <NoteList
            title="Onde é mais relevante"
            notes={doc.strongIn}
            markerColor="var(--color-up)"
          />
          <NoteList title="Onde engana" notes={doc.weakIn} markerColor="var(--color-down)" hollow />

          {doc.caveat && (
            <div>
              <h4 className="mb-2 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-ink-500">
                Como o Smaug calcula
              </h4>
              <p className="text-xs leading-relaxed text-ink-400">{doc.caveat}</p>
            </div>
          )}
        </section>
      </div>
    </div>,
    document.body,
  );
}

/** One reference figure next to the chart — the context a lone reading lacks. */
function Stat({
  label,
  value,
  color,
  hint,
  dashColor,
}: {
  label: string;
  value: string;
  color?: string;
  hint?: string;
  /** Draws the chart's dashed reference line as this stat's legend. */
  dashColor?: string;
}) {
  return (
    <div className="rounded-lg border border-gold-500/8 bg-vault-850 px-3 py-2" title={hint}>
      <div className="flex items-center gap-1.5 text-[0.62rem] uppercase tracking-wide text-ink-600">
        {dashColor && (
          <span
            className="h-0 w-3.5 shrink-0"
            style={{ borderTop: `2px dashed ${dashColor}`, opacity: 0.7 }}
          />
        )}
        {label}
      </div>
      <div
        className="nums mt-0.5 text-base font-semibold"
        style={{ color: color ?? "var(--color-ink-200)" }}
      >
        {value}
      </div>
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
        className="rounded-md px-2 py-1 transition-colors focus-visible:outline-1 focus-visible:outline-gold-500"
        style={{
          backgroundColor: on ? "var(--color-vault-800)" : "transparent",
          color: on ? accent : "var(--color-ink-600)",
        }}
      >
        {icon}
      </button>
    );
  };
  return (
    <div className="flex gap-0.5 rounded-lg border border-gold-500/8 p-0.5">
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
      <h4 className="mb-2.5 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-ink-500">
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
            <p className="text-sm leading-relaxed text-ink-400">
              <span className="text-ink-100">{n.where}</span>
              <span className="text-ink-600"> — </span>
              {n.why}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
