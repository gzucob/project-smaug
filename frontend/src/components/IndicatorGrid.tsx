"use client";

/**
 * The indicator grid for one view (TTM or a closed year).
 *
 * Client-side because each cell exposes two affordances — an evolution chart and
 * the reference doc — that open a shared modal. The per-indicator series is built
 * from the full closed-year history plus the TTM window, so both grids on the
 * page open the same drill-down for a given indicator.
 */
import { useState } from "react";
import type { ReactNode } from "react";
import { FiBarChart2, FiInfo } from "react-icons/fi";
import { IndicatorDetail } from "@/components/IndicatorDetail";
import type { IndicatorSeries } from "@/components/IndicatorDetail";
import { LAST_12M_SHORT, signOf, toNum, yearOf } from "@/lib/format";
import { indicatorDoc } from "@/lib/indicator-docs";
import {
  BASIS_HINT,
  BASIS_LABEL,
  INDICATOR_GROUPS,
  basisPair,
  groupColor,
  specByKey,
  specsByGroup,
} from "@/lib/indicators";
import type { IndicatorSpec } from "@/lib/indicators";
import { reasonCopy } from "@/lib/null-reasons";
import type { Analysis, IndicatorKey, Indicators } from "@/lib/types";

export function IndicatorGrid({
  indicators,
  sector,
  history,
  ttm,
}: {
  indicators: Indicators;
  sector: string;
  history: Analysis[];
  ttm: Analysis | null;
}) {
  const [openKey, setOpenKey] = useState<IndicatorKey | null>(null);

  const seriesFor = (key: IndicatorKey): IndicatorSeries => {
    const labels = history.map((h) => yearOf(h.reference_date));
    const values = history.map((h) => toNum(h.indicators[key]));
    if (ttm) {
      labels.push(LAST_12M_SHORT);
      values.push(toNum(ttm.indicators[key]));
    }
    return { labels, values, ghostLast: ttm !== null };
  };

  const openSpec = openKey ? specByKey(openKey) : undefined;

  return (
    <div className="flex flex-col gap-6">
      {INDICATOR_GROUPS.map((group) => {
        const accent = groupColor(group);
        // A section whose every cell is inapplicable to this filer's regime is
        // not an omission to report — it is a heading that does not belong on
        // this page ("Banco" over three dashes on an oil company). Individual
        // n/d cells stay: each one names its cause and teaches something (#33).
        // A gap of ours (unmapped account, missing input) never hides a section,
        // because it is not `inapplicable_regime`.
        const specs = specsByGroup(group);
        const groupInapplicable = specs.every(
          (s) =>
            toNum(indicators[s.key]) === null &&
            indicators.null_reasons?.[s.key] === "inapplicable_regime",
        );
        if (groupInapplicable) return null;
        return (
          <section key={group}>
            <h4
              className="mb-3 flex items-center gap-2 text-[0.7rem] font-semibold uppercase tracking-[0.18em]"
              style={{ color: accent }}
            >
              <span className="h-px w-4" style={{ backgroundColor: accent }} />
              {group}
            </h4>
            <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-3 xl:grid-cols-4">
              {specs.map((spec) => (
                <IndicatorCell
                  key={spec.key}
                  spec={spec}
                  indicators={indicators}
                  accent={accent}
                  onOpen={() => setOpenKey(spec.key)}
                />
              ))}
            </div>
          </section>
        );
      })}

      {openKey && openSpec && (
        <IndicatorDetail
          spec={openSpec}
          doc={indicatorDoc(openKey)}
          series={seriesFor(openKey)}
          accent={groupColor(openSpec.group)}
          sector={sector}
          nullReason={indicators.null_reasons?.[openKey]}
          onSelectKey={setOpenKey}
          onClose={() => setOpenKey(null)}
        />
      )}
    </div>
  );
}

function IndicatorCell({
  spec,
  indicators,
  accent,
  onOpen,
}: {
  spec: IndicatorSpec;
  indicators: Indicators;
  accent: string;
  onOpen: () => void;
}) {
  const raw = indicators[spec.key];
  const text = spec.format(raw);
  const missing = toNum(raw) === null;
  // The API says *why* a null is null; the cell used to render every one of
  // them as a bare "n/d", which reads as "not applicable" even when the honest
  // answer is "we did not compute it" (#54).
  const reason = missing ? reasonCopy(indicators.null_reasons?.[spec.key]) : null;

  // The consolidated slice (ADR 0026) shows up only when it would actually read
  // differently. The test is the rendered text, not a tolerance: if both bases
  // format to "24,2%", a second line states nothing and only costs height.
  const pair = basisPair(spec.key);
  const totalRaw = pair ? indicators[pair.total] : null;
  const totalText = pair ? spec.format(totalRaw) : "";
  const showTotal = pair !== undefined && toNum(totalRaw) !== null && totalText !== text;

  let valueColor = "var(--color-ink-50)";
  if (missing) valueColor = "var(--color-ink-600)";
  else if (spec.signed) {
    const s = signOf(raw);
    valueColor = s === "up" ? "var(--color-up)" : s === "down" ? "var(--color-down)" : "var(--color-ink-200)";
  }

  return (
    <div
      className="group relative overflow-hidden rounded-xl border border-gold-500/8 bg-vault-900/40 p-3 transition-colors hover:border-gold-500/20"
      title={spec.hint}
    >
      <span
        className="absolute inset-y-0 left-0 w-[3px] opacity-40 transition-opacity group-hover:opacity-90"
        style={{ backgroundColor: accent }}
      />

      <div className="flex items-start justify-between gap-2">
        <div className="text-[0.68rem] font-medium uppercase tracking-wide text-ink-500">
          {spec.label}
        </div>
        <div className="flex shrink-0 gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
          <CellButton label={`Evolução de ${spec.label}`} onClick={onOpen}>
            <FiBarChart2 size={12} />
          </CellButton>
          <CellButton label={`Sobre ${spec.label}`} onClick={onOpen}>
            <FiInfo size={12} />
          </CellButton>
        </div>
      </div>

      <div className="nums mt-1 text-lg font-semibold leading-tight" style={{ color: valueColor }}>
        {text}
      </div>
      {reason && (
        <div
          className="text-[0.6rem]"
          style={{ color: reason.intentional ? "var(--color-ink-600)" : "var(--color-gold-600)" }}
          title={reason.long}
        >
          {reason.short}
        </div>
      )}

      {showTotal && (
        <div
          className="mt-0.5 flex items-baseline gap-1 text-[0.6rem] text-ink-600"
          title={BASIS_HINT.total}
        >
          {BASIS_LABEL.total}
          <span className="nums text-ink-400">{totalText}</span>
        </div>
      )}
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
      className="rounded-md p-1 text-ink-600 transition-colors hover:bg-vault-800 hover:text-gold-300 focus-visible:outline-1 focus-visible:outline-gold-500"
    >
      {children}
    </button>
  );
}
