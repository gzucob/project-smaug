import type { IconType } from "react-icons";
import {
  FiDatabase,
  FiInfo,
  FiTrendingUp,
} from "react-icons/fi";
import { CompanyOverview } from "@/components/CompanyOverview";
import { ClassificationBadge } from "@/components/ClassificationBadge";
import { FavoriteButton } from "@/components/FavoriteButton";
import { HistoryCharts } from "@/components/HistoryCharts";
import { HistoryStrip } from "@/components/HistoryStrip";
import { PriceHistoryChart } from "@/components/PriceHistoryChart";
import { ViewPanel } from "@/components/ViewPanel";
import { VaultOffline } from "@/components/VaultOffline";
import { fetchPortfolioList, fetchPriceHistory, fetchTicker } from "@/lib/api";
import { price, yearOf } from "@/lib/format";
import { gemKey, sectorColor } from "@/lib/sectors";
import type { Analysis } from "@/lib/types";

export async function generateMetadata({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return { title: `${symbol.toUpperCase()} — Smaug` };
}

export default async function TickerPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  const [result, portfolioResult, priceHistoryResult] = await Promise.all([
    fetchTicker(symbol),
    fetchPortfolioList(),
    fetchPriceHistory(symbol),
  ]);

  if (!result.ok) {
    const notFound = result.status === 404;
    return (
      <VaultOffline
        title={notFound ? "Ticker não encontrado" : "Não foi possível carregar a análise"}
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
        title="Sem dados disponíveis"
        message="Este ticker ainda não tem um período atual nem um ano fechado para exibir."
        showBackHome
      />
    );
  }

  const headlinePrice = ttm?.price ?? latestClosed?.price ?? null;
  const favorited =
    portfolioResult.ok &&
    portfolioResult.data.some((p) => p.ticker === result.data.ticker.toUpperCase());
  const sector = gemKey(reference.classification);
  const accent = sectorColor(sector);

  return (
    <>
      <div className="sticky top-16 z-40 border-b border-copy-200/10 bg-canvas-950/95 backdrop-blur-sm">
        <div className="mx-auto max-w-[1480px] px-4 sm:px-6 lg:px-8">
          <AnalysisNavigation showHistory={history.length >= 2} />
        </div>
      </div>

      <div className="mx-auto max-w-[1480px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="min-w-0">
          <header className="border-b border-copy-200/10 pb-7">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2">
                <div className="flex items-center gap-3">
                  <h1 className="nums text-5xl font-semibold tracking-[-0.06em] text-copy-50 sm:text-6xl">
                    {result.data.ticker}
                  </h1>
                  <FavoriteButton ticker={result.data.ticker} favorited={favorited} />
                </div>
                <div className="ml-auto flex items-baseline gap-2">
                  <span className="nums text-2xl font-semibold tracking-tight text-copy-50 sm:text-3xl">
                    {price(headlinePrice)}
                  </span>
                </div>
              </div>
              <p className="mt-2 max-w-xl text-base font-medium text-copy-200">
                {reference.issuer ?? "Análise fundamentalista"}
              </p>
              <div className="mt-4">
                <ClassificationBadge classification={reference.classification} />
              </div>
            </div>
          </header>

          <section id="visao-geral" className="scroll-mt-32 border-b border-copy-200/10 py-8">
            <SectionHeading
              title="Visão geral"
              accent={accent}
            />
            <div className="mt-5">
              <CompanyOverview analysis={reference} />
            </div>
          </section>

          <section id="cotacao" className="scroll-mt-32 border-b border-copy-200/10 py-8">
            <SectionHeading
              title="Cotação histórica"
              accent={accent}
            />
            <div className="mt-5">
              {priceHistoryResult.ok ? (
                <PriceHistoryChart history={priceHistoryResult.data} />
              ) : (
                <p className="text-sm text-copy-600">
                  {priceHistoryResult.status === 404
                    ? "O histórico de cotação deste ativo ainda não está disponível."
                    : "Não foi possível carregar o histórico de cotação agora."}
                </p>
              )}
            </div>
          </section>

          <section
            id="indicadores"
            aria-labelledby="ticker-indicators-heading"
            className="scroll-mt-32 border-b border-copy-200/10 py-8"
          >
            <ViewPanel
              analysis={reference}
              compare={ttm ? latestClosed : null}
              history={history}
              ttm={ttm}
              sectionAccent={accent}
              primary
            />
          </section>

          {history.length >= 2 && (
            <section id="historico" className="scroll-mt-32 border-b border-copy-200/10 py-8">
              <SectionHeading
                title="Evolução dos fundamentos"
                description={`${yearOf(history[0].reference_date)}–${yearOf(history[history.length - 1].reference_date)} · períodos fechados${ttm ? " e período atual" : ""}`}
                accent={accent}
              />
              <div className="mt-5">
                <HistoryCharts history={history} sector={sector} ttm={ttm} />
                {ttm && (
                  <p className="mt-3 text-xs text-copy-600">
                    O período atual é móvel e não corresponde a um exercício fechado.
                  </p>
                )}
              </div>
              <div className="mt-8">
                <HistoryStrip history={history} />
              </div>
            </section>
          )}

        </div>
      </div>
    </>
  );
}

function AnalysisNavigation({ showHistory }: { showHistory: boolean }) {
  return (
    <nav aria-label="Seções da análise" className="min-w-0 overflow-x-auto">
      <div className="flex w-max min-w-full items-center justify-center gap-1 py-2">
        <SectionLink href="#visao-geral" icon={FiInfo} label="Visão geral" />
        <SectionLink href="#cotacao" icon={FiTrendingUp} label="Cotação" />
        <SectionLink href="#indicadores" icon={FiTrendingUp} label="Indicadores" />
        {showHistory ? <SectionLink href="#historico" icon={FiDatabase} label="Histórico" /> : null}
      </div>
    </nav>
  );
}

function SectionLink({
  href,
  icon: Icon,
  label,
}: {
  href: string;
  icon: IconType;
  label: string;
}) {
  return (
    <a
      href={href}
      className="pressable flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-xs font-medium text-copy-500 transition-colors hover:bg-copy-200/5 hover:text-copy-100 focus-visible:outline-1 focus-visible:outline-accent-400"
    >
      <Icon aria-hidden size={14} />
      {label}
    </a>
  );
}

function SectionHeading({
  title,
  description,
  accent,
}: {
  title: string;
  description?: string;
  accent: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-1 h-8 w-1 shrink-0 rounded-full" style={{ backgroundColor: accent }} aria-hidden />
      <div className="min-w-0">
        <h2 className="text-xl font-semibold tracking-tight text-copy-100">{title}</h2>
        {description && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-copy-600">{description}</p>}
      </div>
    </div>
  );
}
