"use client";

import { useState } from "react";
import { FiArrowDown, FiArrowUp, FiTrendingUp } from "react-icons/fi";
import { IndicatorChart } from "@/components/IndicatorChart";
import { dateOnly, price, signedPct, toNum } from "@/lib/format";
import type { PriceHistory } from "@/lib/types";

const PERIODS = [
  { label: "7 dias", months: null, days: 7 },
  { label: "1 mês", months: 1 },
  { label: "6 meses", months: 6 },
  { label: "1 ano", months: 12 },
  { label: "5 anos", months: 60 },
  { label: "10 anos", months: 120 },
  { label: "Tudo", months: null },
] as const;

export function PriceHistoryChart({ history }: { history: PriceHistory }) {
  const [showExtremes, setShowExtremes] = useState(false);
  const [selectedPeriod, setSelectedPeriod] = useState<string>("1 ano");
  const period = PERIODS.find((item) => item.label === selectedPeriod)!;
  const months = period.months;
  const last = history.points.at(-1);
  if (!last) {
    return <p className="text-sm text-copy-600">Não há fechamentos diários resolvidos para este ativo.</p>;
  }

  const end = new Date(`${last.session}T00:00:00Z`);
  const start = new Date(end);
  if ("days" in period) {
    start.setUTCDate(start.getUTCDate() - period.days);
  } else if (months !== null) {
    const day = start.getUTCDate();
    start.setUTCDate(1);
    start.setUTCMonth(start.getUTCMonth() - months);
    const lastDay = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
    start.setUTCDate(Math.min(day, lastDay));
  }
  const floor = period.label === "Tudo" ? "" : start.toISOString().slice(0, 10);
  const points = history.points
    .filter((point) => point.session >= floor)
    .map((point) => ({ label: point.session, value: toNum(point.adjusted) }));
  const first = points[0]?.label ?? last.session;
  const firstValue = points[0]?.value ?? null;
  const lastValue = toNum(last.adjusted);
  const change = points.length > 1 && firstValue !== null && lastValue !== null
    ? lastValue - firstValue : null;
  const relativeChange = change !== null && firstValue !== null && firstValue > 0
    ? change / firstValue : null;
  const changeTitle = `Variação do fechamento ajustado de ${dateOnly(first)} a ${dateOnly(last.session)}`;
  const gaps = history.gaps.filter((gap) =>
    gap.year >= Number(first.slice(0, 4)) && gap.year <= Number(last.session.slice(0, 4)),
  );
  const chart = [
    ...points,
    ...gaps.map((gap) => ({ label: `${gap.year}-01-01`, value: null })),
  ].sort((a, b) => a.label.localeCompare(b.label));

  return (
    <div className="panel p-4 sm:p-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-canvas-800 text-accent-300" aria-hidden>
              <FiTrendingUp size={16} />
            </span>
            <p className="card-title">Último fechamento · {last.session.split("-").reverse().join("/")}</p>
          </div>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p className="nums text-2xl font-semibold text-copy-100">{price(last.adjusted)}</p>
            <span title={changeTitle} aria-live="polite" aria-atomic="true"
              className={`nums inline-flex items-center gap-1 text-sm font-semibold ${change === null || change === 0 ? "text-copy-400" : change > 0 ? "text-positive" : "text-negative"}`}>
              {change === null ? "Variação n/d" : <>
                {change > 0 ? "+" : change < 0 ? "−" : ""}{price(Math.abs(change))}
                {' '}({relativeChange === null ? "n/d" : signedPct(relativeChange, 2)})
                {change > 0 ? <FiArrowUp aria-hidden="true" /> : change < 0 ? <FiArrowDown aria-hidden="true" /> : null}
              </>}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Período da cotação">
          {PERIODS.map((period) => (
            <button
              key={period.label}
              type="button"
              aria-pressed={selectedPeriod === period.label}
              onClick={() => setSelectedPeriod(period.label)}
              className={`pressable min-h-11 rounded-md px-3 py-2 text-xs font-medium focus-visible:outline-2 focus-visible:outline-accent-400 ${
                selectedPeriod === period.label ? "bg-accent-400/10 text-accent-300" : "text-copy-600 hover:bg-copy-200/5 hover:text-copy-200"
              }`}
            >
              {period.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mb-2 flex justify-end">
        <button type="button" aria-pressed={showExtremes} onClick={() => setShowExtremes((shown) => !shown)}
          className={`pressable min-h-11 cursor-pointer rounded-md border px-3 text-xs font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-400 ${showExtremes ? "border-accent-400/60 bg-accent-400/10 text-accent-300 hover:bg-accent-400/15" : "border-copy-400/30 bg-canvas-800 text-copy-200 hover:border-accent-400/60 hover:bg-canvas-700"}`}>
          Máximo e mínimo
        </button>
      </div>
      <div role="group" aria-label={`Fechamento diário ajustado de ${history.ticker}, de ${first} a ${last.session}, em reais.`}>
        <IndicatorChart
          labels={chart.map((point) => point.label)}
          values={chart.map((point) => point.value)}
          ghostLast={false}
          color="var(--color-accent-400)"
          formatKind="price"
          mode="line"
          average={null}
          reference={points[0]?.value != null
            ? { value: points[0].value, label: "Início" }
            : null}
          height={300}
          seriesLabel="Fechamento ajustado"
          dateLabels
          showExtremes={showExtremes}
        />
      </div>
      {gaps.length > 0 && (
        <p className="mt-2 text-xs text-copy-600">
          Sem dados resolvidos em {gaps.map((gap) => gap.year).join(", ")}.
        </p>
      )}
    </div>
  );
}
