import Link from "next/link";
import { HistoryStrip } from "@/components/HistoryStrip";
import { SectorBadge } from "@/components/SectorBadge";
import { ViewPanel } from "@/components/ViewPanel";
import { VaultOffline } from "@/components/VaultOffline";
import { fetchTicker } from "@/lib/api";
import { price, yearOf } from "@/lib/format";
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
    return <VaultOffline title="Sem dados" message="Ticker sem TTM nem anos fechados." showBackHome />;
  }

  const headlinePrice = ttm?.price ?? latestClosed?.price ?? null;

  return (
    <div className="mx-auto max-w-6xl px-5 py-12">
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
            <SectorBadge sector={reference.sector} />
          </div>
        </div>
        <div className="rise text-right" style={{ animationDelay: "120ms" }}>
          <div className="text-xs uppercase tracking-wide text-ink-500">Preço atual</div>
          <div className="nums text-4xl font-semibold text-gold-molten">{price(headlinePrice)}</div>
        </div>
      </header>

      {/* ---------------------------------------------------- two views --- */}
      <section className="mb-14">
        <h2 className="mb-5 flex items-center gap-3 font-display text-2xl text-ink-100">
          Duas visões
          <span className="h-px flex-1 bg-gradient-to-r from-gold-500/30 to-transparent" />
        </h2>
        <div className="grid gap-5 lg:grid-cols-2">
          {ttm && (
            <div className="rise" style={{ animationDelay: "160ms" }}>
              <ViewPanel analysis={ttm} primary />
            </div>
          )}
          {latestClosed && (
            <div className="rise" style={{ animationDelay: "240ms" }}>
              <ViewPanel analysis={latestClosed} primary={!ttm} />
            </div>
          )}
        </div>
        {!ttm && (
          <p className="mt-4 text-sm text-ink-500">
            Sem TTM ao vivo para este ticker — exibindo apenas o histórico de anos fechados.
          </p>
        )}
      </section>

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
          <HistoryStrip history={history} sector={reference.sector} />
        </section>
      )}
    </div>
  );
}
