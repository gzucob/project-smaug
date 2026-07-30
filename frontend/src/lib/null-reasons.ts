/**
 * PT-BR wording for the calculator's null-reason vocabulary (ADR 0008).
 *
 * Every `n/d` on screen used to look the same, which quietly turned three very
 * different statements into one: "this indicator is meaningless for this
 * company", "we could compute it and haven't", and "an input was missing". The
 * first is a domain answer; the other two are our own gaps. The API names the
 * cause per indicator, so the UI can stop guessing — and stop flattering us.
 */
import type { NullReason } from "@/lib/types";

interface ReasonCopy {
  /** Fits under a grid cell's value. */
  short: string;
  /** The tooltip / modal sentence. */
  long: string;
  /** A domain answer rather than a gap on our side. */
  intentional: boolean;
}

const COPY: Record<NullReason, ReasonCopy> = {
  inapplicable_regime: {
    short: "não se aplica",
    long: "Não faz sentido econômico no regime contábil que esta empresa entrega — o cálculo devolve n/d de propósito.",
    intentional: true,
  },
  zero_denominator: {
    short: "divisor zero",
    long: "Todos os insumos existem, mas o divisor é zero neste período — a razão não é definida.",
    intentional: true,
  },
  source_account_unmapped: {
    short: "não implementado",
    long: "A conta existe no arquivo da CVM, mas o Smaug ainda não a lê para este regime. É uma lacuna nossa, não da empresa.",
    intentional: false,
  },
  source_account_absent: {
    short: "conta ausente",
    long: "Procuramos a linha no arquivo entregue à CVM e ela não existe neste período.",
    intentional: false,
  },
  missing_price: {
    short: "sem preço",
    long: "Faltou a cotação deste período na fonte de preços.",
    intentional: false,
  },
  price_symbol_not_found: {
    short: "sem cotação",
    long: "Nenhuma fonte de preço reconheceu este símbolo — papel deslistado ou renomeado, sem equivalência cadastrada.",
    intentional: false,
  },
  missing_share_count: {
    short: "sem nº de ações",
    long: "Faltou a quantidade de ações em circulação (FRE) para este período.",
    intentional: false,
  },
  missing_prior_period: {
    short: "sem período anterior",
    long: "O período anterior não foi ingerido, então a variação não pode ser apurada.",
    intentional: false,
  },
};

const UNCLASSIFIED: ReasonCopy = {
  short: "n/d",
  long: "Nulo sem causa registrada — o cálculo não classificou este vazio.",
  intentional: false,
};

export function reasonCopy(reason: NullReason | undefined): ReasonCopy {
  return reason ? COPY[reason] : UNCLASSIFIED;
}
