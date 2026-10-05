import { DASH, multiple } from "@/lib/format";
import type { Analysis, IndicatorContract, IndicatorKey } from "@/lib/types";

type ValuationCard = {
  key: Extract<IndicatorKey, "pe_basic" | "pb">;
  label: string;
};

const SECURITY_CARDS: ValuationCard[] = [
  { key: "pe_basic", label: "P/L" },
  { key: "pb", label: "P/VP" },
];


const TOKEN_LABEL: Record<string, string> = {
  security_price: "preço do papel",
  market_capitalization: "valor de mercado da companhia",
  selected_basic_eps: "lucro por ação",
  net_income_per_selected_closing_share: "lucro líquido por ação",
  cpc41_basic_eps: "lucro por ação divulgado",
  annualized_attributable_net_income: "lucro atribuível anualizado",
  closing_outstanding_shares: "ações em circulação no fechamento",
  market_convention_basic_eps: "LPA básico estimado",
  closing_attributable_bvps: "VPA de fechamento dos controladores",
  attributable_net_income: "lucro atribuível aos controladores",
  current_attributable_equity: "patrimônio atual dos controladores",
};

const BASIS_LABEL: Record<string, string> = {
  security_selected_evidence: "papel individual",
  security_closing_capital: "lucro líquido e quantidade de ações",
  security_cpc41: "papel individual com resultado divulgado",
  security_market_convention: "papel individual",
  security_closing: "papel individual em base de fechamento",
  company_market_convention: "companhia inteira",
};

const SHARE_BASIS_LABEL: Record<string, string> = {
  selected_weighted_average_class_rights: "média ponderada da classe e direitos",
  selected_closing_total_unit_equivalent: "total de ações, com ajuste para units",
  cpc41_weighted_average_class_rights: "média ponderada da classe e direitos",
  listed_classes_outstanding: "classes listadas e ações em circulação",
};

const PERIOD_LABEL: Record<string, string> = {
  last_twelve_months: "período atual",
  closed_fiscal_year: "ano fechado",
  reference_date_closing: "saldo no fechamento da data de referência",
  cash_rights_window: "janela de datas ex dos direitos B3",
};

const SOURCE_LABEL: Record<string, string> = {
  cvm: "CVM",
  b3: "B3",
};

export function ValuationSummary({ analysis }: { analysis: Analysis }) {
  return (
    <section className="mb-8" aria-labelledby="valuation-title">
      <div className="mb-5 flex items-end gap-3">
        <div>
          <h2 id="valuation-title" className="text-xl font-semibold tracking-tight text-copy-100">
            Preço e valor
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-copy-600">
            Preço comparado com o lucro e o patrimônio por ação.
          </p>
        </div>
        <span className="h-px flex-1 bg-copy-200/10" />
      </div>

      <div className="grid gap-4">
        <ValuationGroup
          title="Dados por ação"
          subtitle="Preço comparado com lucro e patrimônio por ação"
          cards={SECURITY_CARDS}
          analysis={analysis}
          accent="var(--color-pastel-violet)"
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
    <div className="rounded-lg border border-copy-200/10 bg-canvas-900 p-5">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-copy-200">{title}</h3>
          <p className="mt-1 text-xs text-copy-600">{subtitle}</p>
        </div>
        <span
          className="mt-0.5 h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: accent }}
          aria-hidden
        />
      </header>

      <div className="grid gap-2 sm:grid-cols-2">
        {cards.map((card) => (
          <ValuationMetric key={card.key} card={card} analysis={analysis} />
        ))}
      </div>
    </div>
  );
}

function ValuationMetric({ card, analysis }: { card: ValuationCard; analysis: Analysis }) {
  const displayValue = analysis.indicators[card.key];
  const contract = analysis.indicator_contract?.[card.key];
  return (
    <article className="rounded-lg border border-copy-200/10 bg-canvas-950 p-3">
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-[0.68rem] font-medium leading-snug text-copy-500">
          {card.label}
        </h4>
      </div>
      <div className="nums mt-1 text-xl font-semibold text-copy-50">
        {multiple(displayValue)}
      </div>
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
    <div className="mt-3 space-y-1 text-[0.61rem] leading-relaxed text-copy-600">
      <p title={`${numerator} ÷ ${denominator}`}>
        {numerator} ÷ {denominator}
      </p>
      <p>{basis} · {period}</p>
      <p title="A base efetiva da cotação vem da visão analisada; a base de ações segue o contrato do indicador.">
        preço {analysis.price_basis ?? DASH} · {shareBasis}
      </p>
      <p>fontes: {sources || DASH}</p>
    </div>
  );
}
