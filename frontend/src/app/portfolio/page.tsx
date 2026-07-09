import { TickerCard } from "@/components/TickerCard";
import { VaultOffline } from "@/components/VaultOffline";
import { fetchPortfolio } from "@/lib/api";
import { PORTFOLIO, SECTORS } from "@/lib/sectors";
import type { Analysis, SectorKey } from "@/lib/types";

export const metadata = { title: "Carteira — Smaug" };

export default async function PortfolioPage() {
  const result = await fetchPortfolio();

  if (!result.ok) {
    return <VaultOffline message={result.message} />;
  }

  const byTicker = new Map<string, Analysis>();
  for (const a of result.data) byTicker.set(a.ticker.toUpperCase(), a);
  const computed = PORTFOLIO.filter((p) => byTicker.has(p.ticker)).length;

  const sectorsInOrder = Object.keys(SECTORS) as SectorKey[];

  return (
    <div className="mx-auto max-w-6xl px-5 py-14">
      <header className="mb-10 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-gold-500">O tesouro</p>
          <h1 className="mt-2 font-display text-4xl text-ink-50">A carteira</h1>
        </div>
        <p className="nums text-sm text-ink-500">
          <span className="text-gold-300">{computed}</span> de {PORTFOLIO.length} tickers analisados
        </p>
      </header>

      <div className="flex flex-col gap-12">
        {sectorsInOrder.map((key) => {
          const tickers = PORTFOLIO.filter((p) => p.sector === key);
          if (tickers.length === 0) return null;
          const meta = SECTORS[key];
          const color = `var(${meta.colorVar})`;
          return (
            <section key={key}>
              <div className="mb-5 flex items-center gap-3">
                <span className="h-3 w-3 rotate-45" style={{ backgroundColor: color }} />
                <h2 className="font-display text-2xl text-ink-100">{meta.label}</h2>
                <span className="ml-auto h-px flex-1" style={{ background: `linear-gradient(90deg, ${color}40, transparent)` }} />
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {tickers.map((p) => (
                  <TickerCard
                    key={p.ticker}
                    ticker={p.ticker}
                    sector={p.sector}
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
