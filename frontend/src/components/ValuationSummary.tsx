import { DASH, multiple } from "@/lib/format";
import type { Analysis, IndicatorContract, IndicatorKey } from "@/lib/types";
import { reasonCopy } from "@/lib/null-reasons";

type ValuationCard = {
  key: Extract<IndicatorKey, "pe_basic" | "pe_diluted" | "pb" | "company_pe" | "company_pb">;
  label: string;
  fallbackKey?: Extract<IndicatorKey, "pe_basic_market">;
};

const STRICT_CARDS: ValuationCard[] = [
  { key: "pe_basic", label: "P/L básico", fallbackKey: "pe_basic_market" },
  { key: "pe_diluted", label: "P/L diluído" },
  { key: "pb", label: "P/VP" },
];

const MARKET_CARDS: ValuationCard[] = [
  { key: "company_pe", label: "P/L da companhia" },
  { key: "company_pb", label: "P/VP da companhia" },
];

const TOKEN_LABEL: Record<string, string> = {
  security_price: "preço do papel",
  market_capitalization: "valor de mercado da companhia",
  cpc41_basic_eps: "LPA básico CPC 41",
  cpc41_diluted_eps: "LPA diluído CPC 41",
  annualized_attributable_net_income: "lucro atribuível anualizado",
  closing_outstanding_shares: "ações em circulação no fechamento",
  market_convention_basic_eps: "LPA básico estimado",
  closing_attributable_bvps: "VPA de fechamento dos controladores",
  attributable_net_income: "lucro atribuível aos controladores",
  current_attributable_equity: "patrimônio atual dos controladores",
};

const BASIS_LABEL: Record<string, string> = {
  security_cpc41: "papel individual com resultado CPC 41",
  security_market_convention: "papel individual em convenção de mercado",
  security_closing: "papel individual em base de fechamento",
  company_market_convention: "companhia inteira em convenção de mercado",
};

const SHARE_BASIS_LABEL: Record<string, string> = {
  cpc41_weighted_average_class_rights: "média ponderada da classe e direitos CPC 41",
  listed_classes_outstanding: "classes listadas e ações em circulação",
};

const PERIOD_LABEL: Record<string, string> = {
  last_twelve_months: "últimos 12 meses (LTM)",
  closed_fiscal_year: "exercício fechado",
  reference_date_closing: "saldo no fechamento da data de referência",
  cash_rights_window: "janela de datas ex dos direitos B3",
};

const SOURCE_LABEL: Record<string, string> = {
  cvm: "CVM",
  b3: "B3",
};

export function ValuationSummary({ analysis }: { analysis: Analysis }) {
  return (
    <section className="mb-12" aria-labelledby="valuation-title">
      <div className="mb-5 flex items-end gap-3">
        <div>
          <h2 id="valuation-title" className="font-display text-2xl text-ink-100">
            Valuation
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-ink-500">
            Duas leituras convivem: a estrita respeita o papel e a divulgação CPC 41;
            a de convenção resume a companhia pelo valor de mercado e deixa seus
            denominadores explícitos.
          </p>
        </div>
        <span className="h-px flex-1 bg-gradient-to-r from-gold-500/30 to-transparent" />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <ValuationGroup
          title="Base estrita"
          subtitle="P/L por papel e P/VP por ação"
          cards={STRICT_CARDS}
          analysis={analysis}
          accent="var(--color-gem-azure)"
        />
        <ValuationGroup
          title="Convenção de mercado"
          subtitle="Capitalização ÷ resultado ou patrimônio da companhia"
          cards={MARKET_CARDS}
          analysis={analysis}
          accent="var(--color-gold-400)"
        />
      </div>
    </section>
  );
}

function ValuationGroup({
  title,
  subtitle,
  cards,
  analysis,
  accent,
}: {
  title: string;
  subtitle: string;
  cards: ValuationCard[];
  analysis: Analysis;
  accent: string;
}) {
  return (
    <div className="panel p-5">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ink-100">{title}</h3>
          <p className="mt-1 text-xs text-ink-500">{subtitle}</p>
        </div>
        <span
          className="mt-0.5 h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: accent }}
          aria-hidden
        />
      </header>

      <div className="grid gap-2 sm:grid-cols-3">
        {cards.map((card) => (
          <ValuationMetric key={card.key} card={card} analysis={analysis} />
        ))}
      </div>
    </div>
  );
}

function ValuationMetric({ card, analysis }: { card: ValuationCard; analysis: Analysis }) {
  const strictValue = analysis.indicators[card.key];
  const fallbackValue = card.fallbackKey ? analysis.indicators[card.fallbackKey] : null;
  const fallbackActive =
    card.fallbackKey !== undefined &&
    strictValue === null &&
    fallbackValue !== null;
  const displayKey = fallbackActive && card.fallbackKey ? card.fallbackKey : card.key;
  const displayValue = fallbackActive ? fallbackValue : strictValue;
  const contract = analysis.indicator_contract?.[displayKey];
  const reason = analysis.indicators.null_reasons[card.key];
  const reasonLabel = reason ? reasonCopy(reason)?.short : null;
  const tierLabel = fallbackActive
    ? "estimado"
    : contract
    ? contract.tier === "strict"
      ? "estrito"
      : "convenção"
    : "sem contrato";

  return (
    <article className="rounded-xl border border-gold-500/8 bg-vault-950/45 p-3">
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-[0.68rem] font-medium uppercase tracking-wide text-ink-500">
          {card.label}
        </h4>
        <span className="text-[0.58rem] uppercase tracking-wide text-ink-600">
          {tierLabel}
        </span>
      </div>
      <div className="nums mt-1 text-xl font-semibold text-ink-50">
        {multiple(displayValue)}
      </div>
      {fallbackActive && (
        <div
          className="mt-0.5 text-[0.6rem] text-gold-500"
          title="O valor estrito ficou indisponível; este número usa lucro atribuível dividido pelas ações de fechamento."
        >
          fora do CPC 41 · fallback de mercado
        </div>
      )}
      {reasonLabel && <div className="mt-0.5 text-[0.6rem] text-gold-600">{reasonLabel}</div>}
      {contract && <ContractLine contract={contract} analysis={analysis} />}
    </article>
  );
}

function ContractLine({ contract, analysis }: { contract: IndicatorContract; analysis: Analysis }) {
  const numerator = TOKEN_LABEL[contract.numerator] ?? contract.numerator;
  const denominator = TOKEN_LABEL[contract.denominator] ?? contract.denominator;
  const basis = BASIS_LABEL[contract.basis] ?? contract.basis;
  const period = PERIOD_LABEL[contract.reference_period] ?? contract.reference_period;
  const sources = contract.provenance.map((source) => SOURCE_LABEL[source] ?? source).join(" + ");
  const shareBasis =
    contract.share_basis === "analysis.share_count_basis"
      ? `ações ${analysis.share_count_basis ?? DASH}`
      : contract.share_basis === "listed_classes_outstanding"
        ? `ações ${analysis.share_count_basis ?? DASH} · classes listadas`
      : `base de ações ${SHARE_BASIS_LABEL[contract.share_basis] ?? contract.share_basis}`;

  return (
    <div className="mt-3 space-y-1 text-[0.61rem] leading-relaxed text-ink-600">
      <p title={`${numerator} ÷ ${denominator}`}>
        {numerator} ÷ {denominator}
      </p>
      <p>
        {basis} · {period}
      </p>
      <p title="A base efetiva da cotação vem da visão analisada; a base de ações segue o contrato do indicador.">
        preço {analysis.price_basis ?? DASH} · {shareBasis}
      </p>
      <p>fontes: {sources || DASH}</p>
    </div>
  );
}
