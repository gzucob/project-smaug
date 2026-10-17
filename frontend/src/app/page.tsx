import Link from "next/link";
import { Suspense } from "react";
import { BackgroundRefresh } from "@/components/BackgroundRefresh";
import { SectionPlaceholder } from "@/components/SectionPlaceholder";
import { FiBarChart2, FiTag, FiTrendingUp } from "react-icons/fi";
import type { IconType } from "react-icons";
import { CompanyExplorer } from "@/components/CompanyExplorer";
import { VaultOffline } from "@/components/VaultOffline";
import { fetchAnalysisSummaries, fetchDataRevision } from "@/lib/api";
import { money, pct, toNum } from "@/lib/format";
import type { AnalysisSummary } from "@/lib/types";
type SummaryIndicatorKey = keyof AnalysisSummary["indicators"];

export default async function HomePage() {
  const revision = await fetchDataRevision();

  return (
    <div className="page-container flex flex-col gap-8 sm:gap-12">
      <BackgroundRefresh
        initialRevision={revision.ok ? revision.data.revision : null}
      />
      <div className="pt-10 pb-4 sm:pt-16 sm:pb-6">
        <Suspense
          fallback={<SectionPlaceholder label="Explorar empresas" tall />}
        >
          <Explore />
        </Suspense>
      </div>

      <Suspense fallback={<SectionPlaceholder label="Destaques" tall />}>
        <Rankings />
      </Suspense>
    </div>
  );
}

async function Explore() {
  const result = await fetchAnalysisSummaries();
  if (!result.ok) return <VaultOffline message={result.message} />;
  return <CompanyExplorer analyses={result.data} />;
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
        <p role="status" className="panel p-4 text-sm text-copy-400">
          Não foi possível carregar os destaques agora. Tente novamente mais
          tarde.
        </p>
      )}
      {analyses.length > 0 && (
        <section aria-labelledby="home-rankings">
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
          <p className="mb-5 text-sm text-copy-500">
            Entre os ativos disponíveis no Smaug. Indicadores de retorno e
            dividendos dos últimos 12 meses quando disponíveis; o período de
            cada ativo aparece na lista.
          </p>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
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
                  <span className="ml-1">
                    ·{" "}
                    {analysis.view === "ttm_live"
                      ? "12 meses"
                      : analysis.reference_date.slice(0, 4)}
                  </span>
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
