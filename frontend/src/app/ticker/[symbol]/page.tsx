import Link from "next/link";
import type { IconType } from "react-icons";
import {
  FiBookOpen,
  FiDatabase,
  FiInfo,
  FiTrendingUp,
} from "react-icons/fi";
import { AnalysisProvenance } from "@/components/AnalysisProvenance";
import { ClassificationBadge } from "@/components/ClassificationBadge";
import { FavoriteButton } from "@/components/FavoriteButton";
import { HistoryCharts } from "@/components/HistoryCharts";
import { HistoryStrip } from "@/components/HistoryStrip";
import { ViewPanel } from "@/components/ViewPanel";
import { VaultOffline } from "@/components/VaultOffline";
import { fetchPortfolioList, fetchTicker } from "@/lib/api";
import { count, dateTime, money, monthYear, multiple, pct, price, yearOf } from "@/lib/format";
import { gemKey, sectorColor } from "@/lib/sectors";
import type { Analysis } from "@/lib/types";

export async function generateMetadata({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return { title: `${symbol.toUpperCase()} — Smaug` };
}

export default async function TickerPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  const [result, portfolioResult] = await Promise.all([
    fetchTicker(symbol),
    fetchPortfolioList(),
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
  const scale = reference.indicators;
  const scaleFigures: { label: string; value: string }[] = [
    { label: "Valor de mercado", value: money(scale.market_cap) },
    { label: "Valor da firma (EV)", value: money(scale.enterprise_value) },
    { label: "Caixa", value: money(scale.cash_equivalents) },
    { label: "Aplicações", value: money(scale.current_financial_investments) },
    { label: "Ações", value: count(scale.shares) },
  ];

  return (
    <div className="mx-auto max-w-[1480px] px-4 py-8 sm:px-6 lg:px-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <Link
          href="/portfolio"
          className="pressable text-xs font-medium text-copy-500 hover:text-copy-200"
        >
          ← Carteira
        </Link>
        <span className="text-xs text-copy-600">CVM · B3</span>
      </div>

      <div className="grid gap-7 lg:grid-cols-[184px_minmax(0,1fr)] lg:items-start">
        <AnalysisRail />

        <div className="min-w-0">
          <header className="border-b border-copy-200/10 pb-7">
            <div className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="nums text-5xl font-semibold tracking-[-0.06em] text-copy-50 sm:text-6xl">
                    {result.data.ticker}
                  </h1>
                  <FavoriteButton ticker={result.data.ticker} favorited={favorited} />
                </div>
                <p className="mt-2 max-w-xl text-base font-medium text-copy-200">
                  {reference.issuer ?? "Análise fundamentalista"}
                </p>
                <p className="mt-1 text-xs text-copy-500">
                  {reference.cnpj ? `${reference.cnpj} · ` : ""}
                  {reference.cd_cvm ? `CVM ${reference.cd_cvm}` : "fundamentos da CVM"}
                </p>
                <div className="mt-4">
                  <ClassificationBadge classification={reference.classification} />
                </div>
              </div>

              <div className="sm:text-right">
                <p className="text-xs font-medium uppercase tracking-[0.14em] text-copy-600">
                  Cotação atual
                </p>
                <p className="nums mt-2 text-4xl font-semibold tracking-tight text-copy-50">
                  {price(headlinePrice)}
                </p>
                <p className="mt-1 text-xs text-copy-600">
                  {reference.price_source_code ? `B3 ${reference.price_source_code} · ` : ""}
                  {reference.price_source_session ??
                    (ttm ? `referência de ${monthYear(ttm.reference_date)}` : "último ano fechado")}
                </p>
              </div>
            </div>

            <div className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <HeadlineMetric label="P/L" value={multiple(reference.indicators.pe_basic)} />
              <HeadlineMetric label="P/VP" value={multiple(reference.indicators.pb)} />
              <HeadlineMetric label="ROE" value={pct(reference.indicators.roe)} />
              <HeadlineMetric label="Dividend Yield" value={pct(reference.indicators.dividend_yield)} />
            </div>
          </header>

          <section id="visao-geral" className="scroll-mt-24 border-b border-copy-200/10 py-8">
            <SectionHeading
              eyebrow="Visão geral"
              title="Tamanho e referência"
              description="As grandezas que ajudam a situar a empresa antes da leitura dos indicadores."
              accent={accent}
            />
            <div className="mt-5 grid gap-px overflow-hidden rounded-lg border border-copy-200/10 bg-copy-200/10 sm:grid-cols-2 xl:grid-cols-5">
              {scaleFigures.map((figure) => (
                <div key={figure.label} className="bg-canvas-900 px-4 py-4">
                  <p className="text-xs text-copy-600">{figure.label}</p>
                  <p className="nums mt-2 text-lg font-semibold text-copy-200">{figure.value}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-copy-600">
              <span className="inline-flex items-center gap-2">
                <FiInfo aria-hidden size={13} style={{ color: accent }} />
                Valores derivados da visão {ttm ? "atual" : "do último ano fechado"}.
              </span>
              <span>Calculado em {dateTime(reference.computed_at)}</span>
            </div>
          </section>

          <section id="indicadores" className="scroll-mt-24 border-b border-copy-200/10 py-8">
            <SectionHeading
              eyebrow="Leitura por assunto"
              title="Indicadores"
              description="Cada grupo responde a uma pergunta diferente sobre a empresa. Abra um cartão para ver a evolução e a origem do número."
              accent={accent}
            />
            <div className="mt-5">
              <ViewPanel
                analysis={reference}
                compare={ttm ? latestClosed : null}
                history={history}
                ttm={ttm}
                primary
              />
            </div>
            {!ttm && (
              <p className="mt-4 text-xs text-copy-600">
                Este ticker não tem período atual; os indicadores acima mostram o último ano fechado.
              </p>
            )}
          </section>

          {history.length >= 2 && (
            <section id="historico" className="scroll-mt-24 border-b border-copy-200/10 py-8">
              <SectionHeading
                eyebrow="Histórico"
                title="Evolução dos fundamentos"
                description={`${yearOf(history[0].reference_date)}–${yearOf(history[history.length - 1].reference_date)} · períodos fechados${ttm ? " e período atual" : ""}`}
                accent={accent}
              />
              <div className="mt-5">
                <HistoryCharts history={history} sector={sector} ttm={ttm} />
                {ttm && (
                  <p className="mt-3 text-xs text-copy-600">
                    O período atual é uma janela móvel; não representa um ano fechado.
                  </p>
                )}
              </div>
              <div className="mt-8">
                <HistoryStrip history={history} />
              </div>
            </section>
          )}

          <AnalysisProvenance analysis={reference} />
        </div>
      </div>
    </div>
  );
}

function AnalysisRail() {
  return (
    <aside className="lg:sticky lg:top-24">
      <div className="flex gap-1 overflow-x-auto border-b border-copy-200/10 pb-2 lg:block lg:space-y-1 lg:border-0 lg:pb-0">
        <RailLink href="#visao-geral" icon={FiInfo} label="Visão geral" />
        <RailLink href="#indicadores" icon={FiTrendingUp} label="Indicadores" active />
        <RailLink href="#historico" icon={FiDatabase} label="Histórico" />
        <RailLink href="#fontes" icon={FiBookOpen} label="Fontes e método" />
      </div>
    </aside>
  );
}

function RailLink({
  href,
  icon: Icon,
  label,
  active = false,
}: {
  href: string;
  icon: IconType;
  label: string;
  active?: boolean;
}) {
  return (
    <a
      href={href}
      className={`pressable flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-xs font-medium transition-colors lg:w-full ${
        active
          ? "bg-accent-400/10 text-accent-300"
          : "text-copy-600 hover:bg-copy-200/5 hover:text-copy-200"
      }`}
    >
      <Icon aria-hidden size={14} />
      {label}
    </a>
  );
}

function HeadlineMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-copy-200/10 bg-canvas-900 px-4 py-3">
      <p className="text-[0.68rem] font-medium text-copy-600">{label}</p>
      <p className="nums mt-2 text-lg font-semibold text-copy-100">{value}</p>
    </div>
  );
}

function SectionHeading({
  eyebrow,
  title,
  description,
  accent,
}: {
  eyebrow: string;
  title: string;
  description: string;
  accent: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-1 h-8 w-1 shrink-0 rounded-full" style={{ backgroundColor: accent }} aria-hidden />
      <div>
        <p className="text-[0.68rem] font-semibold uppercase tracking-[0.16em]" style={{ color: accent }}>
          {eyebrow}
        </p>
        <h2 className="mt-1 text-xl font-semibold tracking-tight text-copy-100">{title}</h2>
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-copy-600">{description}</p>
      </div>
    </div>
  );
}
