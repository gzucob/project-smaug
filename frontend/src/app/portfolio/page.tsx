import Link from "next/link";
import { TickerCard } from "@/components/TickerCard";
import { VaultOffline } from "@/components/VaultOffline";
import { fetchPortfolio, fetchPortfolioList } from "@/lib/api";
import { SECTORS, gemKey } from "@/lib/sectors";
import type { Analysis, PortfolioTicker, SectorKey } from "@/lib/types";

export const metadata = { title: "Carteira — Smaug" };

export default async function PortfolioPage() {
  const [portfolioResult, analysesResult] = await Promise.all([
    fetchPortfolioList(),
    fetchPortfolio(),
  ]);

  if (!portfolioResult.ok) {
    return <VaultOffline message={portfolioResult.message} />;
  }
  const favorites = portfolioResult.data;

  const byTicker = new Map<string, Analysis>();
  if (analysesResult.ok) {
    for (const a of analysesResult.data) byTicker.set(a.ticker.toUpperCase(), a);
  }

  if (favorites.length === 0) {
    return <EmptyPortfolio />;
  }

  const computed = favorites.filter((p) => byTicker.has(p.ticker)).length;
  const sectorsInOrder = Object.keys(SECTORS) as SectorKey[];
  // Not yet computed has no classification to read a sector from — the same
  // "industry" default the backend falls back to for an unmatched CVM label.
  const sectorOf = (p: PortfolioTicker): SectorKey => {
    const analysis = byTicker.get(p.ticker);
    return analysis ? gemKey(analysis.classification) : "industry";
  };

  return (
    <div className="mx-auto max-w-6xl px-5 py-14">
      <header
        className="rise mb-10 flex flex-wrap items-end justify-between gap-4"
        style={{ animationDelay: "0ms" }}
      >
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-gold-500">
            O tesouro
          </p>
          <h1 className="mt-2 font-display text-4xl text-ink-50">A carteira</h1>
        </div>
        <p className="nums text-sm text-ink-500">
          <span className="text-gold-300">{computed}</span> de{" "}
          {favorites.length} tickers analisados
        </p>
      </header>

      <div className="flex flex-col gap-12">
        {sectorsInOrder
          .filter((key) => favorites.some((p) => sectorOf(p) === key))
          .map((key, index) => {
            const tickers = favorites.filter((p) => sectorOf(p) === key);
            const meta = SECTORS[key];
            const color = `var(${meta.colorVar})`;
            return (
              // Staggered per SECTION, not per card: 45 cards at the app's 60ms
              // step would take 2.6s to settle, far past any entrance's welcome.
              // The filter above runs before the index so an empty sector cannot
              // leave a hole in the cascade (#136).
              <section
                key={key}
                className="rise"
                style={{ animationDelay: `${(index + 1) * 60}ms` }}
              >
                <div className="mb-5 flex items-center gap-3">
                  <span
                    className="h-3 w-3 rotate-45"
                    style={{ backgroundColor: color }}
                  />
                  <h2 className="font-display text-2xl text-ink-100">
                    {meta.label}
                  </h2>
                  <span
                    className="ml-auto h-px flex-1"
                    style={{
                      background: `linear-gradient(90deg, ${color}40, transparent)`,
                    }}
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {tickers.map((p) => (
                    <TickerCard
                      key={p.ticker}
                      ticker={p.ticker}
                      sector={key}
                      analysis={byTicker.get(p.ticker) ?? null}
                    />
                  ))}
                </div>
              </section>
            );
          })}
      </div>
    </div>
  );
}

function EmptyPortfolio() {
  return (
    <div className="mx-auto max-w-2xl px-5 py-24 text-center">
      <p className="rise text-xs font-semibold uppercase tracking-[0.3em] text-gold-500">
        O tesouro
      </p>
      <h1 className="rise mt-2 font-display text-3xl text-ink-50">
        A carteira está vazia
      </h1>
      <p className="rise mt-4 text-ink-400">
        Busque um ticker e toque no coração na página dele para começar a
        guardar seu tesouro.
      </p>
      <Link
        href="/"
        className="pressable mt-8 inline-block rounded-lg border border-gold-500/20 px-4 py-2 text-sm font-semibold text-gold-300 hover:border-gold-400/50"
      >
        Buscar um ticker
      </Link>
    </div>
  );
}
