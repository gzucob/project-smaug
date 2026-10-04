"use client";

/**
 * The indicator grid for one view (current period or a closed year).
 *
 * Client-side because each cell exposes two affordances — an evolution chart and
 * the reference doc — that open a shared modal. The per-indicator series is built
 * from the full closed-year history plus the current rolling window, so both grids on the
 * page open the same drill-down for a given indicator.
 */
import { useState } from "react";
import type { ReactNode } from "react";
import { FiBarChart2, FiChevronDown, FiDatabase, FiGrid, FiInfo, FiStar } from "react-icons/fi";
import { IndicatorDetail } from "@/components/IndicatorDetail";
import type { IndicatorSeries } from "@/components/IndicatorDetail";
import { LAST_12M_SHORT, toNum, yearOf } from "@/lib/format";
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
import type { Analysis, IndicatorContract, IndicatorKey, Indicators } from "@/lib/types";

export function IndicatorGrid({
  indicators,
  indicatorContract,
  compare,
  compareLabel,
  sector,
  history,
  ttm,
}: {
  indicators: Indicators;
  indicatorContract?: Partial<Record<IndicatorKey, IndicatorContract>>;
  /** The closed exercise each cell is measured against — null on that exercise itself. */
  compare: Indicators | null;
  /** That exercise's year, named once in the header rather than in 29 cells. */
  compareLabel: string | null;
  sector: string;
  history: Analysis[];
  ttm: Analysis | null;
}) {
  const [openKey, setOpenKey] = useState<IndicatorKey | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [starredKeys, setStarredKeys] = useState<Set<IndicatorKey>>(new Set());

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

  const visibleGroups = INDICATOR_GROUPS.flatMap((group) => {
    const specs = specsByGroup(group);
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

  function toggleStar(key: IndicatorKey) {
    setStarredKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="flex flex-col gap-6">
      <IndicatorTabs allCollapsed={allCollapsed} onToggleAll={toggleAllGroups} />

      {visibleGroups.map(({ group, specs, accent }) => {
        const collapsed = collapsedGroups.has(group);
        return (
          <section
            key={group}
            className="overflow-hidden rounded-md border border-copy-200/10 bg-canvas-950"
          >
            <button
              type="button"
              aria-expanded={!collapsed}
              onClick={() => toggleGroup(group)}
              className="pressable relative flex w-full items-center justify-between gap-4 border-b border-copy-200/10 border-l-4 px-4 py-3 text-left hover:bg-copy-200/5 sm:px-5"
              style={{
                borderLeftColor: accent,
                backgroundColor: `color-mix(in oklab, ${accent} 10%, var(--color-canvas-850))`,
              }}
            >
              <span className="text-[0.7rem] font-bold uppercase tracking-[0.02em] text-copy-100">
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
              <div className="grid gap-px bg-copy-200/10 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {specs.map((spec) => (
                  <IndicatorCell
                    key={spec.key}
                    spec={spec}
                    indicators={indicators}
                    starred={starredKeys.has(spec.key)}
                    onToggleStar={() => toggleStar(spec.key)}
                    onOpen={() => setOpenKey(spec.key)}
                  />
                ))}
              </div>
            )}
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
          contract={indicatorContract?.[openKey]}
          nullReason={indicators.null_reasons?.[openKey]}
          previous={compare?.[openKey] ?? null}
          previousLabel={compareLabel}
          onSelectKey={setOpenKey}
          onClose={() => setOpenKey(null)}
        />
      )}
    </div>
  );
}

function IndicatorTabs({
  allCollapsed,
  onToggleAll,
}: {
  allCollapsed: boolean;
  onToggleAll: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-copy-200/10 pb-2">
      <a
        href="#indicadores"
        className="inline-flex items-center gap-2 border-b-2 border-accent-400 pb-2 text-[0.72rem] font-bold uppercase tracking-[0.02em] text-accent-300"
      >
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent-400/10">
          <FiGrid aria-hidden size={14} />
        </span>
        Indicadores
      </a>
      <span className="h-5 w-px bg-copy-200/20" aria-hidden />
      <a
        href="#historico"
        className="inline-flex items-center gap-2 pb-2 text-[0.72rem] font-bold uppercase tracking-[0.02em] text-copy-400 hover:text-copy-200"
      >
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-copy-200/5">
          <FiDatabase aria-hidden size={14} />
        </span>
        Histórico de indicadores
      </a>
      <button
        type="button"
        onClick={onToggleAll}
        className="pressable ml-auto pb-2 text-[0.68rem] font-semibold text-accent-400 hover:text-accent-300"
      >
        {allCollapsed ? "Expandir tudo" : "Recolher tudo"}
      </button>
    </div>
  );
}

function groupHeading(group: string): string {
  return `Indicadores de ${group}`;
}

function IndicatorCell({
  spec,
  indicators,
  starred,
  onToggleStar,
  onOpen,
}: {
  spec: IndicatorSpec;
  indicators: Indicators;
  starred: boolean;
  onToggleStar: () => void;
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

  // Neutral ink, always. The sign is already in the glyph; colouring it too made
  // the growth cells read as alerts among thirty neutral ones — see the note in
  // `lib/indicators.ts`.
  const valueColor = missing
    ? "var(--color-copy-600)"
    : "var(--color-copy-50)";

  return (
    <div
      className="group relative min-h-[92px] bg-canvas-900 px-4 py-4 transition-colors hover:bg-canvas-850 sm:px-5"
      title={spec.hint}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-start gap-1.5">
            <div className="max-w-[24ch] text-[0.7rem] font-bold uppercase leading-snug text-copy-100">
              {spec.label}
            </div>
            <CellButton label={`Sobre ${spec.label}`} onClick={onOpen}>
              <FiInfo size={14} />
            </CellButton>
          </div>
          <div className="nums mt-1.5 text-sm font-semibold leading-tight" style={{ color: valueColor }}>
            {text}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1 pt-4">
          <CellButton
            label={starred ? `Desmarcar ${spec.label}` : `Marcar ${spec.label}`}
            onClick={onToggleStar}
            active={starred}
          >
            <FiStar size={16} fill={starred ? "currentColor" : "none"} />
          </CellButton>
          <CellButton label={`Evolução de ${spec.label}`} onClick={onOpen}>
            <FiBarChart2 size={16} />
          </CellButton>
        </div>
      </div>
      {reason && (
        <div
          className="mt-1 text-[0.61rem]"
          style={{ color: reason.intentional ? "var(--color-copy-600)" : "var(--color-warning)" }}
          title={reason.long}
        >
          {reason.short}
        </div>
      )}

      {showTotal && (
        <div
          className="mt-1 flex items-baseline gap-1 text-[0.63rem] text-copy-600"
          title={BASIS_HINT.total}
        >
          {BASIS_LABEL.total}
          <span className="nums text-copy-400">{totalText}</span>
        </div>
      )}
    </div>
  );
}

function CellButton({
  label,
  onClick,
  active = false,
  children,
}: {
  label: string;
  onClick: () => void;
  active?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={`pressable rounded-md p-1 focus-visible:outline-1 focus-visible:outline-accent-500 ${
        active
          ? "bg-accent-400/10 text-accent-300"
          : "text-copy-500 hover:bg-canvas-800 hover:text-copy-200"
      }`}
    >
      {children}
    </button>
  );
}
