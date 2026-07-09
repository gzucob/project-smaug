/**
 * Display metadata for each computed indicator: PT-BR label, group, and how to
 * format it. Growth indicators are sign-colored (up/down); the rest stay
 * neutral because coloring them "good/bad" without sector-aware thresholds
 * would mislead — the domain deliberately leaves that judgement out.
 */
import { money, multiple, pct, price, signedPct } from "@/lib/format";
import type { Decimalish, IndicatorKey } from "@/lib/types";

export type IndicatorGroup =
  | "Rentabilidade"
  | "Por ação"
  | "Crescimento"
  | "Alavancagem & Liquidez"
  | "Múltiplos de mercado"
  | "Fluxo de caixa";

export interface IndicatorSpec {
  key: IndicatorKey;
  label: string;
  hint: string;
  group: IndicatorGroup;
  format: (v: Decimalish) => string;
  signed?: boolean; // color by sign of the value
}

export const INDICATORS: IndicatorSpec[] = [
  { key: "roe", label: "ROE", hint: "Retorno sobre o patrimônio líquido", group: "Rentabilidade", format: pct },
  { key: "roa", label: "ROA", hint: "Retorno sobre os ativos", group: "Rentabilidade", format: pct },
  { key: "roic", label: "ROIC", hint: "Retorno sobre o capital investido (NOPAT / capital investido)", group: "Rentabilidade", format: pct },
  { key: "net_margin", label: "Margem líquida", hint: "Lucro líquido / receita", group: "Rentabilidade", format: pct },
  { key: "gross_margin", label: "Margem bruta", hint: "Lucro bruto / receita", group: "Rentabilidade", format: pct },
  { key: "ebit_margin", label: "Margem EBIT", hint: "EBIT (lucro operacional) / receita", group: "Rentabilidade", format: pct },
  { key: "ebitda_margin", label: "Margem EBITDA", hint: "EBITDA / receita", group: "Rentabilidade", format: pct },
  { key: "asset_turnover", label: "Giro do ativo", hint: "Receita / ativo total — quantas vezes o ativo gira em vendas no ano", group: "Rentabilidade", format: multiple },

  { key: "eps", label: "LPA", hint: "Lucro por ação (lucro líquido / número de ações)", group: "Por ação", format: price },
  { key: "bvps", label: "VPA", hint: "Valor patrimonial por ação (patrimônio / número de ações)", group: "Por ação", format: price },

  { key: "revenue_growth", label: "Cresc. receita", hint: "Variação da receita frente ao ano anterior", group: "Crescimento", format: signedPct, signed: true },
  { key: "net_income_growth", label: "Cresc. lucro", hint: "Variação do lucro frente ao ano anterior", group: "Crescimento", format: signedPct, signed: true },

  { key: "net_debt", label: "Dívida líquida", hint: "Dívida total − caixa e aplicações", group: "Alavancagem & Liquidez", format: money },
  { key: "net_debt_to_ebitda", label: "Dív. líq./EBITDA", hint: "Anos de EBITDA para quitar a dívida líquida", group: "Alavancagem & Liquidez", format: multiple },
  { key: "debt_to_equity", label: "Dív. bruta/PL", hint: "Dívida total / patrimônio líquido", group: "Alavancagem & Liquidez", format: multiple },
  { key: "liabilities_to_assets", label: "Passivo/Ativo", hint: "Passivo total / ativo total — fatia dos ativos financiada por terceiros", group: "Alavancagem & Liquidez", format: pct },
  { key: "current_ratio", label: "Liquidez corrente", hint: "Ativo circulante / passivo circulante", group: "Alavancagem & Liquidez", format: multiple },

  { key: "pe", label: "P/L", hint: "Preço / lucro", group: "Múltiplos de mercado", format: multiple },
  { key: "pb", label: "P/VP", hint: "Preço / valor patrimonial", group: "Múltiplos de mercado", format: multiple },
  { key: "psr", label: "P/Receita", hint: "Valor de mercado / receita (PSR)", group: "Múltiplos de mercado", format: multiple },
  { key: "price_to_assets", label: "P/Ativo", hint: "Valor de mercado / ativo total", group: "Múltiplos de mercado", format: multiple },
  { key: "price_to_ebit", label: "P/EBIT", hint: "Valor de mercado / lucro operacional (EBIT)", group: "Múltiplos de mercado", format: multiple },
  { key: "price_to_working_capital", label: "P/Cap. giro", hint: "Valor de mercado / capital de giro (ativo circ. − passivo circ.)", group: "Múltiplos de mercado", format: multiple },
  { key: "payout", label: "Payout", hint: "Proventos pagos / lucro líquido — fatia do lucro distribuída", group: "Múltiplos de mercado", format: pct },
  { key: "dividend_yield", label: "Dividend yield", hint: "Proventos / valor de mercado", group: "Múltiplos de mercado", format: pct },
  { key: "ev_ebitda", label: "EV/EBITDA", hint: "Valor da firma / EBITDA", group: "Múltiplos de mercado", format: multiple },

  { key: "fcf", label: "Fluxo de caixa livre", hint: "Caixa operacional − investimentos em ativos (CAPEX)", group: "Fluxo de caixa", format: money },
  { key: "price_to_fcf", label: "P/FCL", hint: "Valor de mercado / fluxo de caixa livre", group: "Fluxo de caixa", format: multiple },
  { key: "fcf_yield", label: "FCF yield", hint: "Fluxo de caixa livre / valor de mercado", group: "Fluxo de caixa", format: pct },
];

export const INDICATOR_GROUPS: IndicatorGroup[] = [
  "Rentabilidade",
  "Por ação",
  "Crescimento",
  "Alavancagem & Liquidez",
  "Múltiplos de mercado",
  "Fluxo de caixa",
];

export function specsByGroup(group: IndicatorGroup): IndicatorSpec[] {
  return INDICATORS.filter((s) => s.group === group);
}

export function specByKey(key: IndicatorKey): IndicatorSpec | undefined {
  return INDICATORS.find((s) => s.key === key);
}
