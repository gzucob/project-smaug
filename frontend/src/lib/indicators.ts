/**
 * Display metadata for each computed indicator: PT-BR label, group, and how to
 * format it. Growth indicators are sign-colored (up/down); the rest stay
 * neutral because coloring them "good/bad" without sector-aware thresholds
 * would mislead — the domain deliberately leaves that judgement out.
 */
import { money, multiple, pct, signedPct } from "@/lib/format";
import type { Decimalish, IndicatorKey } from "@/lib/types";

export type IndicatorGroup =
  | "Rentabilidade"
  | "Crescimento"
  | "Alavancagem & Liquidez"
  | "Múltiplos de mercado";

export interface IndicatorSpec {
  key: IndicatorKey;
  label: string;
  hint: string;
  group: IndicatorGroup;
  format: (v: Decimalish) => string;
  signed?: boolean; // color by sign of the value
}

export const INDICATORS: IndicatorSpec[] = [
  { key: "roe", label: "ROE", hint: "Retorno sobre patrimônio", group: "Rentabilidade", format: pct },
  { key: "roa", label: "ROA", hint: "Retorno sobre ativos", group: "Rentabilidade", format: pct },
  { key: "net_margin", label: "Margem líquida", hint: "Lucro / receita", group: "Rentabilidade", format: pct },
  { key: "gross_margin", label: "Margem bruta", hint: "Lucro bruto / receita", group: "Rentabilidade", format: pct },
  { key: "ebitda_margin", label: "Margem EBITDA", hint: "EBITDA / receita", group: "Rentabilidade", format: pct },

  { key: "revenue_growth", label: "Cresc. receita", hint: "Variação YoY da receita", group: "Crescimento", format: signedPct, signed: true },
  { key: "net_income_growth", label: "Cresc. lucro", hint: "Variação YoY do lucro", group: "Crescimento", format: signedPct, signed: true },

  { key: "net_debt", label: "Dívida líquida", hint: "Dívida − caixa", group: "Alavancagem & Liquidez", format: money },
  { key: "net_debt_to_ebitda", label: "Dív. líq./EBITDA", hint: "Alavancagem", group: "Alavancagem & Liquidez", format: multiple },
  { key: "current_ratio", label: "Liquidez corrente", hint: "Ativo circ. / passivo circ.", group: "Alavancagem & Liquidez", format: multiple },

  { key: "pe", label: "P/L", hint: "Preço / lucro", group: "Múltiplos de mercado", format: multiple },
  { key: "pb", label: "P/VP", hint: "Preço / valor patrimonial", group: "Múltiplos de mercado", format: multiple },
  { key: "dividend_yield", label: "Dividend yield", hint: "Proventos / preço", group: "Múltiplos de mercado", format: pct },
  { key: "ev_ebitda", label: "EV/EBITDA", hint: "Valor da firma / EBITDA", group: "Múltiplos de mercado", format: multiple },
];

export const INDICATOR_GROUPS: IndicatorGroup[] = [
  "Rentabilidade",
  "Crescimento",
  "Alavancagem & Liquidez",
  "Múltiplos de mercado",
];

export function specsByGroup(group: IndicatorGroup): IndicatorSpec[] {
  return INDICATORS.filter((s) => s.group === group);
}
