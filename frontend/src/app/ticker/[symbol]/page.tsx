import Link from "next/link";
import { HistoryCharts } from "@/components/HistoryCharts";
import { HistoryStrip } from "@/components/HistoryStrip";
import { ClassificationBadge } from "@/components/ClassificationBadge";
import { ViewPanel } from "@/components/ViewPanel";
import { VaultOffline } from "@/components/VaultOffline";
import { fetchTicker } from "@/lib/api";
import { count, money, price, yearOf } from "@/lib/format";
import { gemKey } from "@/lib/sectors";
import type { Analysis } from "@/lib/types";

export async function generateMetadata({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return { title: `${symbol.toUpperCase()} — Smaug` };
}

export default async function TickerPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  const result = await fetchTicker(symbol);

  if (!result.ok) {
    const notFound = result.status === 404;
    return (
      <VaultOffline
        title={notFound ? "Ticker não encontrado" : "O cofre está fechado"}
        message={
          notFound
            ? `Não há análise computada para ${symbol.toUpperCase()}. Rode o comando \`analyze\` para esse ticker.`
            : result.message
        }
        showBackHome
      />
    );
  }

  const { ttm, history } = result.data;
  const latestClosed = history.length > 0 ? history[history.length - 1] : null;
  const reference: Analysis | null = ttm ?? latestClosed;

  if (!reference) {
    return (
      <VaultOffline
        title="Sem dados"
        message="Ticker sem os últimos 12 meses nem anos fechados."
        showBackHome
      />
    );
  }

  const headlinePrice = ttm?.price ?? latestClosed?.price ?? null;

  // Scale figures (not ratios): show the company's size at the top, from the live
  // view when there is one, else the latest closed year (#25).
  const scale = reference.indicators;
  const scaleFigures: { label: string; value: string }[] = [
    { label: "Valor de mercado", value: money(scale.market_cap) },
    { label: "Enterprise value", value: money(scale.enterprise_value) },
    { label: "Ações", value: count(scale.shares) },
  ];

  return (
    <div className="mx-auto max-w-[1600px] px-5 py-12">
      {/* --------------------------------------------------------- hero --- */}
      <div className="mb-3">
        <Link href="/portfolio" className="text-xs text-ink-500 transition-colors hover:text-gold-300">
          ← Carteira
        </Link>
      </div>

      <header className="mb-10 flex flex-wrap items-end justify-between gap-6">
        <div className="rise" style={{ animationDelay: "0ms" }}>
          <h1 className="nums font-display text-6xl font-bold tracking-tight text-ink-50 sm:text-7xl">
            {result.data.ticker}
          </h1>
          <div className="mt-4">
            <ClassificationBadge classification={reference.classification} />
          </div>
        </div>
        <div className="rise text-right" style={{ animationDelay: "120ms" }}>
          <div className="text-xs uppercase tracking-wide text-ink-500">Preço atual</div>
          <div className="nums text-4xl font-semibold text-gold-molten">{price(headlinePrice)}</div>
        </div>
      </header>

      {/* ------------------------------------------------ scale figures --- */}
      <div className="rise mb-12 flex flex-wrap gap-x-12 gap-y-4 border-t border-ink-900/80 pt-5" style={{ animationDelay: "200ms" }}>
        {scaleFigures.map((f) => (
          <div key={f.label}>
            <div className="text-xs uppercase tracking-wide text-ink-500">{f.label}</div>
            <div className="nums mt-1 text-xl font-medium text-ink-100">{f.value}</div>
          </div>
        ))}
      </div>

      {/* ---------------------------------------------------- two views --- */}
      <section className="mb-14">
        <h2 className="mb-5 flex items-center gap-3 font-display text-2xl text-ink-100">
          Duas visões
          <span className="h-px flex-1 bg-gradient-to-r from-gold-500/30 to-transparent" />
        </h2>
        {/* Stacked, not side-by-side: at full width each grid fits four indicator
            columns, leaving room for the per-cell chart/info affordances. */}
        <div className="flex flex-col gap-5">
          {ttm && (
            <div className="rise" style={{ animationDelay: "160ms" }}>
              <ViewPanel analysis={ttm} history={history} ttm={ttm} primary />
            </div>
          )}
          {latestClosed && (
            <div className="rise" style={{ animationDelay: "240ms" }}>
              <ViewPanel
                analysis={latestClosed}
                history={history}
                ttm={ttm}
                primary={!ttm}
              />
            </div>
          )}
        </div>
        {!ttm && (
          <p className="mt-4 text-sm text-ink-500">
            Sem os últimos 12 meses para este ticker — exibindo apenas o histórico de anos
            fechados.
          </p>
        )}
      </section>

      {/* ------------------------------------------------ annual charts --- */}
      {history.length >= 2 && (
        <section className="mb-14">
          <h2 className="mb-5 flex items-center gap-3 font-display text-2xl text-ink-100">
            Evolução anual
            <span className="text-sm font-normal text-ink-500">
              {yearOf(history[0].reference_date)}–{yearOf(history[history.length - 1].reference_date)}
              {ttm && " · últimos 12 meses"}
            </span>
            <span className="h-px flex-1 bg-gradient-to-r from-gold-500/30 to-transparent" />
          </h2>
          <HistoryCharts history={history} sector={gemKey(reference.classification)} ttm={ttm} />
          {ttm && (
            <p className="mt-3 text-xs text-ink-600">
              A última barra, tracejada, são os últimos 12 meses — uma janela móvel, não um
              exercício fechado.
            </p>
          )}
        </section>
      )}

      {/* ------------------------------------------------------ history --- */}
      {history.length >= 2 && (
        <section>
          <h2 className="mb-5 flex items-center gap-3 font-display text-2xl text-ink-100">
            Trajetória
            <span className="text-sm font-normal text-ink-500">
              {yearOf(history[0].reference_date)}–{yearOf(history[history.length - 1].reference_date)}
            </span>
            <span className="h-px flex-1 bg-gradient-to-r from-gold-500/30 to-transparent" />
          </h2>
          <HistoryStrip history={history} sector={gemKey(reference.classification)} />
        </section>
      )}
    </div>
  );
}
