import Link from "next/link";
import { FiBarChart2, FiClock, FiDatabase, FiTag, FiTrendingUp } from "react-icons/fi";
import type { IconType } from "react-icons";
import { SectorBadge } from "@/components/SectorBadge";
import { TickerSearch } from "@/components/TickerSearch";
import { fetchPortfolio, fetchPortfolioList } from "@/lib/api";
import { money, pct, toNum } from "@/lib/format";
import { SECTORS } from "@/lib/sectors";
import type { Analysis, IndicatorKey } from "@/lib/types";

export default async function HomePage() {
  const [portfolioResult, analysesResult] = await Promise.all([
    fetchPortfolioList(),
    fetchPortfolio(),
  ]);
  const analyses = analysesResult.ok ? analysesResult.data : [];
  const marketLeaders = topBy(analyses, "market_cap");
  const yieldLeaders = topBy(analyses, "dividend_yield");
  const returnLeaders = topBy(analyses, "roe");
  const favoriteTickers = portfolioResult.ok ? portfolioResult.data.slice(0, 5) : [];
  const shortcuts =
    favoriteTickers.length > 0
      ? favoriteTickers.map((p) => p.ticker)
      : marketLeaders.slice(0, 5).map((a) => a.ticker);

  return (
    <div className="mx-auto max-w-7xl px-5">
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

        <p
          className="rise mt-6 max-w-2xl font-display text-lg leading-relaxed text-ink-300 sm:text-xl"
          style={{ animationDelay: "120ms" }}
        >
          Uma leitura clara dos fundamentos da sua carteira.
          <em className="mt-3 block text-accent-300 not-italic">
            Compare o período atual com o histórico de anos fechados.
          </em>
        </p>

        <div className="rise mt-9 w-full max-w-md" style={{ animationDelay: "180ms" }}>
          <TickerSearch />
        </div>

        <p
          className="rise mt-4 text-[0.68rem] uppercase tracking-[0.18em] text-ink-600"
          style={{ animationDelay: "240ms" }}
        >
          Fundamentos da CVM · preços da B3
        </p>

        <div
          className="rise mt-6 flex flex-wrap items-center justify-center gap-2"
          style={{ animationDelay: "300ms" }}
        >
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
          <Link
            href="/portfolio"
            className="pressable rounded-lg px-2.5 py-1 text-xs font-semibold text-accent-300 hover:text-accent-200"
          >
            Ver carteira →
          </Link>
        </div>
      </section>

      <section aria-labelledby="home-snapshot" className="pb-12">
        <div className="rise mb-5 flex items-center gap-3" style={{ animationDelay: "360ms" }}>
          <h2 id="home-snapshot" className="text-xs font-semibold uppercase tracking-[0.3em] text-ink-500">
            Visão da base
          </h2>
          <span className="h-px flex-1 bg-copy-200/10" />
        </div>
        <div className="grid gap-4 lg:grid-cols-[1.3fr_0.7fr]">
          <div className="panel rise p-6" style={{ animationDelay: "420ms" }}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent-400">
                  Pesquisa de ativos
                </p>
                <h2 className="mt-2 font-display text-2xl text-copy-50">Encontre uma empresa e leia o contexto completo.</h2>
                <p className="mt-2 max-w-xl text-sm leading-relaxed text-copy-500">
                  Indicadores, histórico, base de preço, ações em circulação e evidências da CVM em uma única leitura.
                </p>
              </div>
              <FiTrendingUp aria-hidden size={20} className="shrink-0 text-accent-400" />
            </div>
            <div className="mt-5 max-w-md">
              <TickerSearch />
            </div>
          </div>

          <div className="panel rise p-6" style={{ animationDelay: "480ms" }}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-copy-500">Cobertura</p>
                <p className="nums mt-2 text-4xl font-semibold text-copy-50">{analyses.length}</p>
                <p className="mt-1 text-sm text-copy-400">ativos com análise persistida</p>
              </div>
              <FiDatabase aria-hidden size={20} className="text-accent-400" />
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3 border-t border-copy-200/10 pt-4 text-xs">
              <div>
                <p className="text-copy-600">Fundamentos</p>
                <p className="mt-1 font-medium text-copy-300">CVM</p>
              </div>
              <div>
                <p className="text-copy-600">Preços e proventos</p>
                <p className="mt-1 font-medium text-copy-300">B3</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {analyses.length > 0 && (
        <section aria-labelledby="home-rankings" className="pb-12">
          <div className="rise mb-5 flex items-center gap-3" style={{ animationDelay: "540ms" }}>
            <h2 id="home-rankings" className="text-xs font-semibold uppercase tracking-[0.3em] text-ink-500">
              Leituras rápidas
            </h2>
            <span className="h-px flex-1 bg-copy-200/10" />
            <Link href="/portfolio" className="text-xs font-semibold text-accent-300 hover:text-accent-200">
              Ver carteira →
            </Link>
          </div>
          <div className="grid gap-4 xl:grid-cols-3">
            <RankingCard title="Maior valor de mercado" icon={FiBarChart2} items={marketLeaders} kind="money" metric="market_cap" />
            <RankingCard title="Maior retorno sobre patrimônio" icon={FiTrendingUp} items={returnLeaders} kind="pct" metric="roe" />
            <RankingCard title="Maior dividend yield" icon={FiTag} items={yieldLeaders} kind="pct" metric="dividend_yield" />
          </div>
        </section>
      )}

      {/* ------------------------------------------------------ features --- */}
      <section aria-labelledby="home-features" className="pb-10">
        <div className="rise mb-5 flex items-center gap-3" style={{ animationDelay: "360ms" }}>
          <h2
            id="home-features"
            className="text-xs font-semibold uppercase tracking-[0.3em] text-ink-500"
          >
            O que você encontra
          </h2>
          <span className="h-px flex-1 bg-copy-200/10" />
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          <Feature
            delay={420}
            title="Duas janelas de leitura"
            body="Compare o período atual com anos fechados e acompanhe a trajetória da empresa sem perder o contexto."
            accent="var(--color-accent-400)"
            Icon={FiClock}
          />
          <Feature
            delay={480}
            title="Organização por setor"
            body="Uma cor por setor ajuda a localizar rapidamente a composição da carteira e a exposição por segmento."
            accent="var(--color-gem-jade)"
            Icon={FiTag}
          />
          <Feature
            delay={540}
            title="Fundamentos no detalhe"
            body="Retorno, eficiência, crescimento, dívida, preço e caixa preservando a base de cada cálculo."
            accent="var(--color-gem-violet)"
            Icon={FiBarChart2}
          />
        </div>
      </section>

      {/* -------------------------------------------------------- sectors --- */}
      <section
        aria-labelledby="home-sectors"
        className="rise pb-4"
        style={{ animationDelay: "600ms" }}
      >
        <div className="mb-5 flex items-center gap-3">
          <h2
            id="home-sectors"
            className="text-xs font-semibold uppercase tracking-[0.3em] text-ink-500"
          >
            Setores na leitura
          </h2>
          <span className="h-px flex-1 bg-copy-200/10" />
        </div>
        <div className="flex flex-wrap items-center justify-center gap-3">
          {Object.values(SECTORS).map((s) => (
            <SectorBadge key={s.key} sector={s.key} />
          ))}
        </div>
      </section>
    </div>
  );
}

function topBy(analyses: Analysis[], key: IndicatorKey): Analysis[] {
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
  items: Analysis[];
  kind: "money" | "pct";
  metric: IndicatorKey;
}) {
  return (
    <div className="panel rise p-5" style={{ animationDelay: "600ms" }}>
      <header className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-copy-200">{title}</p>
          <p className="mt-1 text-xs text-copy-600">Período atual · maior valor na base</p>
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
              <span className="nums w-5 text-xs text-copy-600">{index + 1}</span>
              <span className="min-w-0 flex-1">
                <span className="nums block text-sm font-semibold text-copy-100">{analysis.ticker}</span>
                <span className="block truncate text-[0.65rem] text-copy-600">
                  {analysis.issuer ?? analysis.classification.segmento ?? "Ativo analisado"}
                </span>
              </span>
              <span className="nums text-sm font-semibold text-copy-200">
                {kind === "money" ? money(analysis.indicators[metric]) : pct(analysis.indicators[metric])}
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Feature({
  title,
  body,
  accent,
  Icon,
  delay,
}: {
  title: string;
  body: string;
  accent: string;
  Icon: IconType;
  delay: number;
}) {
  return (
    <div
      className="panel panel-hover rise flex h-full flex-col p-6"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div
        className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border"
        style={{
          color: accent,
          borderColor: `color-mix(in oklab, ${accent} 28%, transparent)`,
          backgroundColor: `color-mix(in oklab, ${accent} 14%, transparent)`,
        }}
      >
        <Icon aria-hidden size={19} strokeWidth={1.8} />
      </div>
      <h3 className="mb-2 font-display text-xl text-ink-100">{title}</h3>
      <p className="text-sm leading-relaxed text-ink-400">{body}</p>
    </div>
  );
}
