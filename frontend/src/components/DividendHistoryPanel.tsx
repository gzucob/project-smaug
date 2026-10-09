"use client";

import { useState } from "react";
import { IndicatorChart } from "@/components/IndicatorChart";
import {
  DASH,
  LAST_12M_SHORT,
  dateOnly,
  pct,
  toNum,
  yearOf,
} from "@/lib/format";
import type { Analysis, CashDividendEvent, CashDividendHistory } from "@/lib/types";

type HistoryRange = "5" | "10" | "max";

const PER_SHARE_FORMAT = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 8,
});

const EVENT_LABELS: Record<string, string> = {
  DIVIDENDO: "Dividendo",
  "JRS CAP PROPRIO": "JCP",
  RENDIMENTO: "Rendimento",
  "REST CAP DIN": "Restituição de capital",
};

function eventLabel(event: CashDividendEvent): string {
  const key = event.event_type?.trim().toUpperCase() ?? "";
  return EVENT_LABELS[key] ?? (key || "Provento");
}

function amountPerShare(value: CashDividendEvent["amount_per_share"]): string {
  const amount = toNum(value);
  return amount === null ? DASH : `R$ ${PER_SHARE_FORMAT.format(amount)}`;
}

function coverageMessage(history: CashDividendHistory | null): string {
  if (history === null) return "Não foi possível carregar os eventos da B3.";
  if (history.coverage === "unresolved") {
    return "A classe deste ticker não está resolvida para consultar os eventos.";
  }
  if (history.coverage === "unavailable") {
    return "A cobertura de proventos da B3 não está confirmada para esta classe.";
  }
  return "A B3 confirmou cobertura, mas não há eventos de caixa para esta classe.";
}

export function DividendHistoryPanel({
  history,
  ttm,
  cashHistory,
}: {
  history: Analysis[];
  ttm: Analysis | null;
  cashHistory: CashDividendHistory | null;
}) {
  const [historyRange, setHistoryRange] = useState<HistoryRange>("5");
  const closedHistory =
    historyRange === "max" ? history : history.slice(-Number(historyRange));
  const periods = ttm ? [...closedHistory, ttm] : closedHistory;
  const labels = periods.map((period) =>
    period === ttm ? LAST_12M_SHORT : yearOf(period.reference_date),
  );
  const distributions = periods.map((period) =>
    toNum(period.indicators.distributions_per_security),
  );
  const yields = periods.map((period) => toNum(period.indicators.dividend_yield));
  const hasDistributions = distributions.some((value) => value !== null);
  const hasYields = yields.some((value) => value !== null);
  const latestEvent =
    cashHistory?.coverage === "available" ? cashHistory.events[0] ?? null : null;
  const latestAnalysis = ttm ?? history[history.length - 1] ?? null;
  const yieldLabel = ttm ? "Dividend Yield · últimos 12 meses" : "Dividend Yield · último exercício";
  const events = cashHistory?.coverage === "available" ? cashHistory.events : [];

  return (
    <section
      aria-labelledby="ticker-dividend-history-heading"
      className="panel flex min-w-0 flex-col gap-5 p-4 sm:p-5"
    >
      <header className="flex flex-wrap items-center justify-between gap-x-5 gap-y-3 pb-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="relative flex items-center gap-2 after:absolute after:inset-x-0 after:-bottom-2.5 after:h-0.5 after:bg-accent-500">
            <span
              aria-hidden="true"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-canvas-800 text-accent-300"
            >
              <span className="nums text-xs font-semibold">R$</span>
            </span>
            <h3
              id="ticker-dividend-history-heading"
              className="card-title"
            >
              Histórico de proventos
            </h3>
          </div>
          <span className="rounded-full border border-accent-400 px-3 py-1 text-[0.65rem] font-semibold tracking-wide text-accent-300">
            ANUAL
          </span>
        </div>

        <div
          className="inline-flex items-center border-b border-vault-700"
          role="group"
          aria-label="Período do histórico de proventos"
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

      <p className="-mt-3 text-xs leading-relaxed text-copy-500">
        Proventos por papel na base atual da análise; eventos listados como a B3 os informou.
      </p>

      <div className="grid min-w-0 gap-5 lg:grid-cols-2">
        <div className="min-w-0 border-t border-copy-200/10 pt-3">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-copy-500">
            Proventos por papel
          </h4>
          {hasDistributions ? (
            <IndicatorChart
              labels={labels}
              values={distributions}
              ghostLast={ttm !== null}
              color="var(--color-up)"
              formatKind="price"
              mode="bars"
              average={null}
              height={190}
            />
          ) : (
            <EmptyChart />
          )}
        </div>

        <div className="min-w-0 border-t border-copy-200/10 pt-3">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-copy-500">
            Dividend Yield
          </h4>
          {hasYields ? (
            <IndicatorChart
              labels={labels}
              values={yields}
              ghostLast={ttm !== null}
              color="var(--color-gem-gold)"
              formatKind="pct"
              mode="line"
              average={null}
              height={190}
            />
          ) : (
            <EmptyChart />
          )}
        </div>
      </div>

      <div className="grid gap-5 border-y border-copy-200/10 py-4 sm:grid-cols-2">
        <div className="border-l-2 border-accent-400 pl-3">
          <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-copy-500">
            Último provento informado
          </p>
          <p className="nums mt-1 text-lg font-medium text-copy-100">
            {latestEvent ? amountPerShare(latestEvent.amount_per_share) : DASH}
          </p>
          <p className="mt-1 text-xs text-copy-500">
            {latestEvent
              ? `${eventLabel(latestEvent)} · com direito até ${dateOnly(latestEvent.last_with_right)}`
              : coverageMessage(cashHistory)}
          </p>
        </div>
        <div className="border-l-2 border-accent-400 pl-3">
          <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-copy-500">
            {yieldLabel}
          </p>
          <p className="nums mt-1 text-lg font-medium text-copy-100">
            {latestAnalysis ? pct(latestAnalysis.indicators.dividend_yield) : DASH}
          </p>
          <p className="mt-1 text-xs text-copy-500">
            Calculado pela análise com proventos B3 e preço do papel.
          </p>
        </div>
      </div>

      <div className="min-w-0">
        <div className="mb-2 flex items-baseline justify-between gap-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-copy-500">
            Eventos recentes
          </h4>
          {events.length > 0 && (
            <span className="nums text-xs text-copy-600">{events.length} eventos</span>
          )}
        </div>

        {events.length > 0 ? (
          <div
            role="region"
            className="max-h-[22rem] overflow-auto rounded-md border border-copy-200/10 bg-canvas-900"
            tabIndex={0}
            aria-label="Tabela histórica de eventos de proventos"
          >
            <table className="w-full min-w-[860px] border-collapse text-sm">
              <caption className="sr-only">
                Eventos de proventos B3 por classe e valor bruto por papel
              </caption>
              <thead className="sticky top-0 z-10 bg-canvas-800">
                <tr className="border-b border-copy-200/10">
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-copy-500 sm:px-4">
                    Tipo
                  </th>
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-copy-500">
                    Classe
                  </th>
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-copy-500">
                    Com direito até
                  </th>
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-copy-500">
                    Pagamento
                  </th>
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-copy-500">
                    Deliberado em
                  </th>
                  <th scope="col" className="px-3 py-3 text-right text-xs font-medium text-copy-500 sm:px-4">
                    Valor bruto · base da época
                  </th>
                </tr>
              </thead>
              <tbody>
                {events.map((event, index) => (
                  <tr
                    key={`${event.share_class}-${event.last_with_right}-${event.event_type}-${index}`}
                    className="border-b border-copy-200/5 last:border-0"
                  >
                    <td className="px-3 py-3 text-copy-200 sm:px-4">{eventLabel(event)}</td>
                    <td className="px-3 py-3 text-copy-400">{event.share_class}</td>
                    <td className="nums whitespace-nowrap px-3 py-3 text-copy-300">
                      {dateOnly(event.last_with_right)}
                    </td>
                    <td className="nums whitespace-nowrap px-3 py-3 text-copy-400">
                      {event.payment_dates.length > 0
                        ? event.payment_dates.map(dateOnly).join(", ")
                        : DASH}
                    </td>
                    <td className="nums whitespace-nowrap px-3 py-3 text-copy-500">
                      {dateOnly(event.approval_date)}
                    </td>
                    <td className="nums whitespace-nowrap px-3 py-3 text-right text-copy-100 sm:px-4">
                      {amountPerShare(event.amount_per_share)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div
            role="status"
            className="rounded-md border border-copy-200/10 bg-canvas-900 px-4 py-5 text-sm text-copy-500"
          >
            {coverageMessage(cashHistory)}
          </div>
        )}
      </div>

      <p className="text-xs leading-relaxed text-copy-600">
        As datas de pagamento aparecem quando constam no suplemento B3 para o ISIN do papel; a ausência de data no espelho não confirma ausência de pagamento. O valor informado é bruto, na base de ações da época; os gráficos usam os valores por papel e o DY persistido na análise.
      </p>
    </section>
  );
}

function EmptyChart() {
  return (
    <div
      className="flex items-center justify-center text-center text-xs text-copy-500"
      style={{ height: 190 }}
      role="status"
    >
      Sem histórico disponível para este indicador.
    </div>
  );
}
