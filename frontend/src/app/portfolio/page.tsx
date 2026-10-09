import Link from "next/link";
import { TickerCard } from "@/components/TickerCard";
import { VaultOffline } from "@/components/VaultOffline";
import { fetchPortfolio, fetchPortfolioList } from "@/lib/api";
import { SECTORS, gemKey } from "@/lib/sectors";
import type { Analysis, PortfolioTicker, SectorKey } from "@/lib/types";

export const metadata = { title: "Favoritos — Smaug" };

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
  const sectorOf = (p: PortfolioTicker): SectorKey | "unclassified" => {
    const analysis = byTicker.get(p.ticker);
    return analysis ? gemKey(analysis.classification) : "unclassified";
  };
  const groups = [
    ...sectorsInOrder.map((key) => ({
      key,
      label: SECTORS[key].label,
    })),
    { key: "unclassified", label: "Sem classificação" },
  ].map((group) => ({
    ...group,
    tickers: favorites.filter((favorite) => sectorOf(favorite) === group.key),
  })).filter((group) => group.tickers.length > 0);

  return (
    <div className="mx-auto max-w-6xl px-5 py-14">
      <header
        className="rise mb-10 flex flex-wrap items-end justify-between gap-4"
        style={{ animationDelay: "0ms" }}
      >
        <div>
          <h1 className="text-4xl font-semibold tracking-tight text-copy-50">Favoritos</h1>
        </div>
        <p className="nums text-sm text-ink-500">
          {analysesResult.ok ? (
            <><span className="text-accent-300">{computed}</span> de {favorites.length} favoritos analisados</>
          ) : (
            <>{favorites.length} favoritos · análises indisponíveis</>
          )}
        </p>
      </header>

      {!analysesResult.ok && (
        <p role="status" className="panel mb-6 p-4 text-sm text-copy-400">
          Não foi possível carregar as análises agora. Seus favoritos continuam salvos.
          Tente novamente mais tarde.
        </p>
      )}

      <div className="flex flex-col gap-24">
        {groups.map(({ key, label, tickers }, index) => {
          return (
            // Staggered per SECTION, not per card: 45 cards at the app's 60ms
            // step would take 2.6s to settle, far past any entrance's welcome.
            // The filter above runs before the index so an empty sector cannot
            // leave a hole in the cascade (#136).
            <section
              key={key}
              aria-label={label}
              className="rise"
              style={{ animationDelay: `${(index + 1) * 60}ms` }}
            >
              <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
                {tickers.map((p) => (
                  <TickerCard
                    key={p.ticker}
                    ticker={p.ticker}
                    sector={key}
                    analysis={byTicker.get(p.ticker) ?? null}
                    unavailable={!analysesResult.ok}
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
      <h1 className="rise text-3xl font-semibold tracking-tight text-copy-50">
        Você ainda não tem favoritos
      </h1>
      <p className="rise mt-4 text-ink-400">
        Busque um ticker e adicione-o aos favoritos para começar a acompanhar seus ativos.
      </p>
      <Link
        href="/"
        className="pressable mt-8 inline-block rounded-lg border border-accent-500/30 px-4 py-2 text-sm font-semibold text-accent-300 hover:border-accent-400/60"
      >
        Buscar um ticker
      </Link>
    </div>
  );
}
