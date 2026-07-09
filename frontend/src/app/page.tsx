import Link from "next/link";
import { DragonMark } from "@/components/DragonMark";
import { SectorBadge } from "@/components/SectorBadge";
import { TickerSearch } from "@/components/TickerSearch";
import { PORTFOLIO, SECTORS } from "@/lib/sectors";

export default function HomePage() {
  return (
    <div className="mx-auto max-w-6xl px-5">
      {/* ---------------------------------------------------------- hero --- */}
      <section className="relative flex flex-col items-center pt-20 pb-16 text-center sm:pt-28">
        <div className="rise mb-8" style={{ animationDelay: "0ms" }}>
          <DragonMark size={120} />
        </div>

        <p
          className="rise mb-4 text-xs font-semibold uppercase tracking-[0.4em] text-gold-500"
          style={{ animationDelay: "80ms" }}
        >
          Análise da carteira
        </p>

        <h1
          className="rise font-brand text-5xl font-bold tracking-[0.18em] text-gold-molten sm:text-7xl"
          style={{ animationDelay: "140ms" }}
        >
          SMAUG
        </h1>

        <p
          className="rise mt-6 max-w-xl font-display text-lg leading-relaxed text-ink-300 sm:text-xl"
          style={{ animationDelay: "220ms" }}
        >
          O dragão que guarda a sua carteira. Cada ação é uma joia do tesouro —
          avaliada em <em className="text-gold-300 not-italic">duas visões</em>: os
          últimos 12 meses e o histórico de anos fechados.
        </p>

        <div className="rise mt-9 w-full max-w-md" style={{ animationDelay: "300ms" }}>
          <TickerSearch />
        </div>

        <div className="rise mt-6 flex flex-wrap items-center justify-center gap-2" style={{ animationDelay: "380ms" }}>
          <span className="text-xs text-ink-600">Atalhos:</span>
          {PORTFOLIO.slice(0, 5).map((p) => (
            <Link
              key={p.ticker}
              href={`/ticker/${p.ticker}`}
              className="nums rounded-lg border border-gold-500/15 px-2.5 py-1 text-xs font-semibold tracking-wide text-ink-300 transition-colors hover:border-gold-400/50 hover:text-gold-300"
            >
              {p.ticker}
            </Link>
          ))}
          <Link
            href="/portfolio"
            className="rounded-lg px-2.5 py-1 text-xs font-semibold text-gold-400 transition-colors hover:text-gold-300"
          >
            ver carteira →
          </Link>
        </div>
      </section>

      {/* ------------------------------------------------------ features --- */}
      <section className="grid gap-4 pb-8 md:grid-cols-3">
        <Feature
          delay={440}
          title="Duas visões"
          body="Os últimos 12 meses, ao preço atual, lado a lado com o histórico de anos fechados. Comparação honesta entre o agora e a trajetória."
          accent="var(--color-ember-500)"
          glyph="◐"
        />
        <Feature
          delay={520}
          title="Sistema de gemas"
          body="Cada setor da carteira tem sua própria pedra preciosa e cor viva — safira, ametista, esmeralda, ouro e rubi — para ler a alocação de relance."
          accent="var(--color-gem-jade)"
          glyph="◆"
        />
        <Feature
          delay={600}
          title="14 indicadores"
          body="Rentabilidade, crescimento, alavancagem e múltiplos de mercado. Razões como fração fiel — a formatação é do front, o cálculo é do domínio."
          accent="var(--color-gem-violet)"
          glyph="⛭"
        />
      </section>

      {/* -------------------------------------------------------- sectors --- */}
      <section className="rise pb-4" style={{ animationDelay: "680ms" }}>
        <div className="hairline mb-6" />
        <div className="flex flex-wrap items-center justify-center gap-3">
          {Object.values(SECTORS).map((s) => (
            <SectorBadge key={s.key} sector={s.key} />
          ))}
        </div>
      </section>
    </div>
  );
}

function Feature({
  title,
  body,
  accent,
  glyph,
  delay,
}: {
  title: string;
  body: string;
  accent: string;
  glyph: string;
  delay: number;
}) {
  return (
    <div className="panel panel-hover rise p-6" style={{ animationDelay: `${delay}ms` }}>
      <div
        className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl text-xl"
        style={{ color: accent, backgroundColor: `color-mix(in oklab, ${accent} 14%, transparent)` }}
      >
        {glyph}
      </div>
      <h3 className="mb-2 font-display text-xl text-ink-100">{title}</h3>
      <p className="text-sm leading-relaxed text-ink-400">{body}</p>
    </div>
  );
}
