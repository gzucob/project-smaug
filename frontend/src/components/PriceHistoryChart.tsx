"use client";

import { useState } from "react";
import { IndicatorChart } from "@/components/IndicatorChart";
import { price, toNum } from "@/lib/format";
import type { PriceHistory } from "@/lib/types";

const PERIODS = [
  { label: "1 mês", months: 1 },
  { label: "6 meses", months: 6 },
  { label: "1 ano", months: 12 },
  { label: "5 anos", months: 60 },
  { label: "Tudo", months: null },
] as const;

export function PriceHistoryChart({ history }: { history: PriceHistory }) {
  const [months, setMonths] = useState<number | null>(12);
  const last = history.points.at(-1);
  if (!last) {
    return <p className="text-sm text-copy-600">Não há fechamentos diários resolvidos para este ativo.</p>;
  }

  const end = new Date(`${last.session}T00:00:00Z`);
  const start = new Date(end);
  if (months !== null) {
    const day = start.getUTCDate();
    start.setUTCDate(1);
    start.setUTCMonth(start.getUTCMonth() - months);
    const lastDay = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
    start.setUTCDate(Math.min(day, lastDay));
  }
  const floor = months === null ? "" : start.toISOString().slice(0, 10);
  const points = history.points
    .filter((point) => point.session >= floor)
    .map((point) => ({ label: point.session, value: toNum(point.adjusted) }));
  const first = points[0]?.label ?? last.session;
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
          <p className="text-xs text-copy-600">Último fechamento · {last.session.split("-").reverse().join("/")}</p>
          <p className="nums mt-1 text-2xl font-semibold text-copy-100">{price(last.adjusted)}</p>
        </div>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Período da cotação">
          {PERIODS.map((period) => (
            <button
              key={period.label}
              type="button"
              aria-pressed={months === period.months}
              onClick={() => setMonths(period.months)}
              className={`pressable rounded-md px-3 py-2 text-xs font-medium focus-visible:outline-2 focus-visible:outline-accent-400 ${
                months === period.months ? "bg-accent-400/10 text-accent-300" : "text-copy-600 hover:bg-copy-200/5 hover:text-copy-200"
              }`}
            >
              {period.label}
            </button>
          ))}
        </div>
      </div>
      <div role="img" aria-label={`Fechamento diário ajustado de ${history.ticker}, de ${first} a ${last.session}, em reais.`}>
        <IndicatorChart
          labels={chart.map((point) => point.label)}
          values={chart.map((point) => point.value)}
          ghostLast={false}
          color="var(--color-accent-400)"
          formatKind="price"
          mode="line"
          average={null}
          height={300}
          seriesLabel="Fechamento ajustado"
          dateLabels
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
