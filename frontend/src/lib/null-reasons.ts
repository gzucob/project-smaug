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
  not_yet_listed: {
    short: "ainda não listado",
    long: "O papel não era negociado neste exercício — começou a ser depois. Não existe cotação a buscar, em fonte nenhuma.",
    // A fact about the world, not a gap of ours: the only price cause that is
    // deliberate. Colouring it as a warning would flag 94 cells nobody can fix.
    intentional: true,
  },
  missing_share_count: {
    short: "sem nº de ações",
    long: "Faltou a quantidade de ações em circulação (FRE) para este período.",
    intentional: false,
  },
  missing_prior_period: {
    short: "sem período anterior",
    long: "O histórico não cobre a janela que este indicador precisa — falta o período anterior para a variação, ou faltam exercícios fechados para fechar a janela do crescimento composto.",
    intentional: false,
  },
  non_positive_endpoint: {
    short: "extremo não positivo",
    long: "Um dos dois extremos da janela é zero ou negativo, e não existe taxa composta a partir daí — a razão entre eles não tem raiz real, e entre dois negativos ela reportaria um prejuízo que aumentou como se fosse crescimento.",
    intentional: true,
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
