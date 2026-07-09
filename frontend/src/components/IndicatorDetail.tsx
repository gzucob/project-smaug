"use client";

/**
 * Modal drill-down for a single indicator: its evolution across the closed-year
 * history (plus the TTM window as a trailing ghost bar) and the reference doc —
 * formula as computed, what it measures, and where it carries meaning across the
 * B3 subsectors.
 *
 * One modal serves both indicator grids on the page: the series and the doc are
 * properties of the indicator, not of the view it was clicked from.
 */
import { useEffect } from "react";
import { FiAlertTriangle, FiX } from "react-icons/fi";
import { YearBars } from "@/components/YearBars";
import { money } from "@/lib/format";
import type { IndicatorDoc, RelevanceNote } from "@/lib/indicator-docs";
import type { IndicatorSpec } from "@/lib/indicators";
import { sectorMeta } from "@/lib/sectors";
import type { SectorKey } from "@/lib/types";

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
  onClose,
}: {
  spec: IndicatorSpec;
  doc: IndicatorDoc;
  series: IndicatorSeries;
  accent: string;
  sector: string;
  onClose: () => void;
}) {
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

  // Bar labels drop the "R$ " prefix to save width, as in HistoryCharts.
  const isMoney = spec.format === money;
  const barFormat = (n: number) =>
    isMoney ? spec.format(n).replace("R$ ", "") : spec.format(n);

  const plottable = series.values.filter((v) => v !== null).length;
  const notApplicable = doc.naSectors?.includes(sector as SectorKey) ?? false;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-vault-950/85 p-4 sm:p-8"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="panel my-auto w-full max-w-3xl p-6 sm:p-7"
        role="dialog"
        aria-modal="true"
        aria-label={spec.label}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ------------------------------------------------------- header --- */}
        <header className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="mt-1.5 h-8 w-[3px] rounded-full" style={{ backgroundColor: accent }} />
            <div>
              <h3 className="font-display text-2xl text-ink-50">{spec.label}</h3>
              <p className="mt-0.5 text-xs text-ink-500">{spec.group}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Fechar"
            className="rounded-lg border border-gold-500/10 p-2 text-ink-500 transition-colors hover:border-gold-500/30 hover:text-ink-200"
          >
            <FiX size={16} />
          </button>
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
          <h4 className="mb-3 text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-ink-500">
            Evolução
          </h4>
          {plottable >= 2 ? (
            <>
              <div className="rounded-xl border border-gold-500/8 bg-vault-900/40 px-3 pb-1 pt-4">
                <YearBars
                  labels={series.labels}
                  values={series.values}
                  color={accent}
                  format={barFormat}
                  ghostLast={series.ghostLast}
                />
              </div>
              {series.ghostLast && (
                <p className="mt-2 text-[0.68rem] text-ink-600">
                  A barra tracejada é a janela TTM (últimos 12 meses), não um exercício fechado.
                </p>
              )}
            </>
          ) : (
            <p className="rounded-xl border border-gold-500/8 bg-vault-900/40 p-4 text-xs text-ink-600">
              Série insuficiente: são necessários ao menos dois períodos com valor apurado.
            </p>
          )}
        </section>

        <div className="hairline my-6" />

        {/* ---------------------------------------------------------- doc --- */}
        <section className="flex flex-col gap-6">
          <div>
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
