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
import { FiBarChart2, FiChevronDown, FiInfo } from "react-icons/fi";
import { IndicatorDetail } from "@/components/IndicatorDetail";
import type { IndicatorSeries } from "@/components/IndicatorDetail";
import { LAST_12M_SHORT, toNum, yearOf } from "@/lib/format";
import { indicatorDoc } from "@/lib/indicator-docs";
import {
  INDICATOR_GROUPS,
  groupColor,
  specByKey,
  specsByGroup,
} from "@/lib/indicators";
import type { IndicatorSpec } from "@/lib/indicators";
import type { Analysis, IndicatorContract, IndicatorKey, Indicators } from "@/lib/types";

export function IndicatorGrid({
  indicators,
  indicatorContract,
  compare,
  compareLabel,
  history,
  ttm,
  isCurrent,
  sectionAccent,
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
  sectionAccent: string;
}) {
  const [openKey, setOpenKey] = useState<IndicatorKey | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  const seriesFor = (key: IndicatorKey): IndicatorSeries => {
    if (key === "tag_along") {
      return { labels: ["Atual"], values: [toNum(indicators[key])], ghostLast: false };
    }
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
    <div className="flex flex-col gap-6">
      <IndicatorToolbar
        allCollapsed={allCollapsed}
        onToggleAll={toggleAllGroups}
        sectionAccent={sectionAccent}
      />

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

function IndicatorToolbar({
  allCollapsed,
  onToggleAll,
  sectionAccent,
}: {
  allCollapsed: boolean;
  onToggleAll: () => void;
  sectionAccent: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-copy-200/10 pb-4">
      <div className="flex min-w-0 items-start gap-3">
        <span
          className="mt-1 h-8 w-1 shrink-0 rounded-full"
          style={{ backgroundColor: sectionAccent }}
          aria-hidden
        />
        <h2
          id="ticker-indicators-heading"
          className="text-xl font-semibold tracking-tight text-copy-100"
        >
          Indicadores
        </h2>
      </div>
      <button
        type="button"
        onClick={onToggleAll}
        className="pressable shrink-0 rounded-md px-2.5 py-1.5 text-[0.68rem] font-semibold text-accent-300 hover:bg-copy-200/5 hover:text-accent-200 focus-visible:outline-1 focus-visible:outline-accent-400"
      >
        {allCollapsed ? "Expandir tudo" : "Recolher tudo"}
      </button>
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
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-start gap-1.5">
            <div className="min-w-0 text-xs font-medium leading-snug text-copy-400">
              {spec.label}
            </div>
            <CellButton label={`Sobre ${spec.label}`} onClick={onOpen}>
              <FiInfo size={14} />
            </CellButton>
          </div>
          <div className="nums mt-2 text-base font-semibold leading-tight" style={{ color: valueColor }}>
            {text}
          </div>
        </div>
        {spec.key !== "tag_along" ? (
          <CellButton label={`Evolução de ${spec.label}`} onClick={onOpen}>
            <FiBarChart2 size={16} />
          </CellButton>
        ) : null}
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
      className="pressable -mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-copy-500 hover:bg-canvas-800 hover:text-copy-200 focus-visible:outline-1 focus-visible:outline-accent-500"
    >
      {children}
    </button>
  );
}
