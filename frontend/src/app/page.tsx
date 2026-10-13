import Link from "next/link";
import { Suspense } from "react";
import { BackgroundRefresh } from "@/components/BackgroundRefresh";
import { SectionPlaceholder } from "@/components/SectionPlaceholder";
import { FiBarChart2, FiTag, FiTrendingUp } from "react-icons/fi";
import type { IconType } from "react-icons";
import { TickerSearch } from "@/components/TickerSearch";
import {
  fetchAnalysisSummaries,
  fetchDataRevision,
  fetchPortfolioList,
} from "@/lib/api";
import { money, pct, toNum } from "@/lib/format";
import type { AnalysisSummary } from "@/lib/types";
type SummaryIndicatorKey = keyof AnalysisSummary["indicators"];

export default async function HomePage() {
  const revision = await fetchDataRevision();

  return (
    <div className="mx-auto max-w-7xl px-5">
      <BackgroundRefresh
        initialRevision={revision.ok ? revision.data.revision : null}
      />
      {/* ---------------------------------------------------------- hero --- */}
      <section className="relative flex flex-col items-center pt-20 pb-16 text-center sm:pt-28">
        <p
          className="rise mb-6 text-xs font-semibold uppercase tracking-[0.4em] text-accent-400"
          style={{ animationDelay: "0ms" }}
        >
          Análise fundamentalista
        </p>

        <h1
          className="rise font-brand text-5xl font-bold tracking-[0.18em] text-gold-molten sm:text-7xl"
          style={{ animationDelay: "60ms" }}
        >
          SMAUG
        </h1>

        <div
          className="rise mt-7 w-full max-w-md"
          style={{ animationDelay: "120ms" }}
        >
          <TickerSearch />
        </div>

        <div
          className="rise mt-5 flex flex-wrap items-center justify-center gap-2"
          style={{ animationDelay: "180ms" }}
        >
          <Suspense fallback={null}>
            <Shortcuts />
          </Suspense>
          <Link
            href="/portfolio"
            className="pressable rounded-lg px-2.5 py-1 text-xs font-semibold text-accent-300 hover:text-accent-200"
          >
            Ver favoritos →
          </Link>
        </div>
      </section>

      <Suspense fallback={<SectionPlaceholder label="Destaques" tall />}>
        <Rankings />
      </Suspense>
    </div>
  );
}

async function Shortcuts() {
  const [portfolioResult, analysesResult] = await Promise.all([
    fetchPortfolioList(),
    fetchAnalysisSummaries(),
  ]);
  const favorites = portfolioResult.ok ? portfolioResult.data.slice(0, 5) : [];
  const shortcuts =
    favorites.length > 0
      ? favorites.map((item) => item.ticker)
      : topBy(analysesResult.ok ? analysesResult.data : [], "market_cap").map(
          (item) => item.ticker,
        );
  return (
    <>
      {" "}
      {shortcuts.length > 0 && (
        <>
          <span className="text-xs text-ink-600">Acesso rápido:</span>
          {shortcuts.map((ticker) => (
            <Link
              key={ticker}
              href={`/ticker/${ticker}`}
              className="pressable nums rounded-lg border border-copy-200/15 px-2.5 py-1 text-xs font-semibold tracking-wide text-copy-300 hover:border-accent-400/50 hover:text-accent-300"
            >
              {ticker}
            </Link>
          ))}
        </>
      )}
    </>
  );
}

async function Rankings() {
  const analysesResult = await fetchAnalysisSummaries();
  const analyses = analysesResult.ok ? analysesResult.data : [];
  const marketLeaders = topBy(analyses, "market_cap");
  const yieldLeaders = topBy(analyses, "dividend_yield");
  const returnLeaders = topBy(analyses, "roe");
  return (
    <>
      {" "}
      {!analysesResult.ok && (
        <p role="status" className="panel mb-12 p-4 text-sm text-copy-400">
          Não foi possível carregar os destaques agora. Tente novamente mais
          tarde.
        </p>
      )}
      {analyses.length > 0 && (
        <section aria-labelledby="home-rankings" className="pb-12">
          <div
            className="rise mb-5 flex items-center gap-3"
            style={{ animationDelay: "240ms" }}
          >
            <h2
              id="home-rankings"
              className="text-xs font-semibold uppercase tracking-[0.3em] text-ink-500"
            >
              Destaques
            </h2>
            <span className="h-px flex-1 bg-copy-200/10" />
          </div>
          <div className="grid grid-cols-1 gap-8 xl:grid-cols-3">
            <RankingCard
              title="Maior valor de mercado"
              icon={FiBarChart2}
              items={marketLeaders}
              kind="money"
              metric="market_cap"
            />
            <RankingCard
              title="Maior retorno sobre patrimônio"
              icon={FiTrendingUp}
              items={returnLeaders}
              kind="pct"
              metric="roe"
            />
            <RankingCard
              title="Maior dividend yield"
              icon={FiTag}
              items={yieldLeaders}
              kind="pct"
              metric="dividend_yield"
            />
          </div>
        </section>
      )}
    </>
  );
}

function topBy(
  analyses: AnalysisSummary[],
  key: SummaryIndicatorKey,
): AnalysisSummary[] {
  return analyses
    .filter((analysis) => toNum(analysis.indicators[key]) !== null)
    .sort((left, right) => {
      const a = toNum(left.indicators[key]) ?? Number.NEGATIVE_INFINITY;
      const b = toNum(right.indicators[key]) ?? Number.NEGATIVE_INFINITY;
      return b - a;
    })
    .slice(0, 5);
}

function RankingCard({
  title,
  icon: Icon,
  items,
  kind,
  metric,
}: {
  title: string;
  icon: IconType;
  items: AnalysisSummary[];
  kind: "money" | "pct";
  metric: SummaryIndicatorKey;
}) {
  return (
    <div className="panel min-w-0 p-5">
      <header className="flex items-start justify-between gap-3">
        <div>
          <p className="card-title">{title}</p>
        </div>
        <Icon aria-hidden size={16} className="text-accent-400" />
      </header>
      <ol className="mt-4 divide-y divide-copy-200/10">
        {items.map((analysis, index) => (
          <li key={analysis.ticker}>
            <Link
              href={`/ticker/${analysis.ticker}`}
              className="pressable flex items-center gap-3 py-3 first:pt-0 last:pb-0"
            >
              <span className="nums w-5 text-xs text-copy-600">
                {index + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="nums block text-sm font-semibold text-copy-100">
                  {analysis.ticker}
                </span>
                <span className="block truncate text-[0.65rem] text-copy-600">
                  {analysis.issuer ??
                    analysis.classification.segmento ??
                    "Ativo analisado"}
                </span>
              </span>
              <span className="nums text-sm font-semibold text-copy-200">
                {kind === "money"
                  ? money(analysis.indicators[metric])
                  : pct(analysis.indicators[metric])}
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}
