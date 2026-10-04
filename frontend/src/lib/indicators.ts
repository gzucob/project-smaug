/**
 * Display metadata for each computed indicator: PT-BR label, group, and how to
 * format it.
 *
 * **Every cell takes the neutral ink.** Growth used to be sign-coloured, which
 * read fine while the only signed cells on screen happened to be negative — the
 * moment the compounded rates arrived (#144) the grid grew four bright blue
 * values among thirty-odd cream ones, and they read as alerts rather than as
 * numbers. The sign is already in the glyph (`signedPct` writes the `+`/`−`);
 * colour on top of it was a second encoding of the same fact, competing with the
 * group accent for attention.
 *
 * Colour still carries direction where there is movement to see: the marks in a
 * chart, and the favourable/treacherous markers in the indicator docs. A grid
 * cell is a value, not a movement.
 */
import { money, multiple, pct, price, signedPct } from "@/lib/format";
import type { Decimalish, IndicatorKey } from "@/lib/types";

export type IndicatorGroup =
  | "Valuation"
  | "Endividamento"
  | "Eficiência"
  | "Rentabilidade"
  | "Crescimento";

export interface IndicatorGroupMeta {
  title: string;
  description: string;
}

/**
 * The same muted sector palette used by the portfolio, applied here as a
 * classification system rather than as a judgement about the number. A group
 * owns a hue; the value itself stays neutral so colour does not imply "good"
 * or "bad".
 */
export const INDICATOR_GROUP_META: Record<IndicatorGroup, IndicatorGroupMeta> = {
  Valuation: {
    title: "Valuation",
    description: "Preço em relação ao lucro, patrimônio, receita, caixa e proventos.",
  },
  Endividamento: {
    title: "Endividamento",
    description: "Dívida, estrutura de capital e capacidade de pagar obrigações.",
  },
  Eficiência: {
    title: "Eficiência",
    description: "Margens, uso dos ativos, geração de caixa e custos da operação.",
  },
  Rentabilidade: {
    title: "Rentabilidade",
    description: "Retorno sobre o patrimônio, os ativos e o capital investido.",
  },
  Crescimento: {
    title: "Crescimento",
    description: "Evolução da receita, do lucro e dos resultados operacionais.",
  },
};

export function indicatorGroupMeta(group: IndicatorGroup): IndicatorGroupMeta {
  return INDICATOR_GROUP_META[group];
}

export interface IndicatorSpec {
  key: IndicatorKey;
  label: string;
  hint: string;
  group: IndicatorGroup;
  format: (v: Decimalish) => string;
}

/**
 * The two statement slices (ADR 0026).
 *
 * A bare indicator name is always the **controllers'** slice — what accrues to
 * the listed shares. Its `_total` sibling is the consolidated group, minority
 * interest included. Neither is the "right" one: they answer different
 * questions, so a screen must say which it is showing rather than pick silently.
 */
export type Basis = "controllers" | "total";

export const BASIS_LABEL: Record<Basis, string> = {
  controllers: "controladores",
  total: "consolidado",
};

export const BASIS_HINT: Record<Basis, string> = {
  controllers: "Fatia dos controladores — o que cabe às ações listadas. Pareia com LPA, VPA e os múltiplos de mercado.",
  total: "Grupo consolidado, incluindo a parcela dos acionistas minoritários das controladas. Pareia resultado e patrimônio na mesma fatia.",
};

/** Indicators published on both slices, keyed by the controllers' name. */
const TOTAL_SIBLING: Partial<Record<IndicatorKey, IndicatorKey>> = {
  roe: "roe_total",
  roa: "roa_total",
  net_margin: "net_margin_total",
  net_income: "net_income_total",
};

const CONTROLLERS_SIBLING: Partial<Record<IndicatorKey, IndicatorKey>> = Object.fromEntries(
  Object.entries(TOTAL_SIBLING).map(([controllers, total]) => [total, controllers]),
);

/** Both names of an indicator that has two bases — undefined when it has one. */
export function basisPair(
  key: IndicatorKey,
): { controllers: IndicatorKey; total: IndicatorKey } | undefined {
  const total = TOTAL_SIBLING[key];
  if (total) return { controllers: key, total };
  const controllers = CONTROLLERS_SIBLING[key];
  if (controllers) return { controllers, total: key };
  return undefined;
}

export function basisOf(key: IndicatorKey): Basis {
  return CONTROLLERS_SIBLING[key] ? "total" : "controllers";
}

export const INDICATORS: IndicatorSpec[] = [
  { key: "pe_basic", label: "P/L", hint: "Preço do papel / lucro por ação", group: "Valuation", format: multiple },
  { key: "pb", label: "P/VP", hint: "Preço do papel / valor patrimonial por papel", group: "Valuation", format: multiple },
  { key: "psr", label: "PSR", hint: "Valor de mercado / receita (PSR)", group: "Valuation", format: multiple },
  { key: "price_to_assets", label: "P/Ativo", hint: "Valor de mercado / ativo total", group: "Valuation", format: multiple },
  { key: "price_to_ebit", label: "P/EBIT", hint: "Valor de mercado / lucro operacional (EBIT)", group: "Valuation", format: multiple },
  { key: "price_to_ebitda", label: "P/EBITDA", hint: "Valor de mercado / EBITDA", group: "Valuation", format: multiple },
  { key: "price_to_working_capital", label: "P/Capital de giro", hint: "Valor de mercado / capital de giro (ativo circ. − passivo circ.)", group: "Valuation", format: multiple },
  { key: "price_to_cfo", label: "P/FCO", hint: "Valor de mercado / fluxo de caixa operacional", group: "Valuation", format: multiple },
  { key: "price_to_fcf", label: "P/FCL", hint: "Valor de mercado / fluxo de caixa livre", group: "Valuation", format: multiple },
  { key: "ev_ebitda", label: "EV/EBITDA", hint: "Valor da firma / EBITDA", group: "Valuation", format: multiple },
  { key: "ev_ebit", label: "EV/EBIT", hint: "Valor da firma / lucro operacional (EBIT)", group: "Valuation", format: multiple },
  { key: "ev_cfo", label: "EV/FCO", hint: "Valor da firma / fluxo de caixa operacional", group: "Valuation", format: multiple },
  { key: "ev_fcf", label: "EV/FCL", hint: "Valor da firma / fluxo de caixa livre", group: "Valuation", format: multiple },
  { key: "ev_revenue", label: "EV/Receita líquida", hint: "Valor da firma / receita líquida", group: "Valuation", format: multiple },
  { key: "earnings_yield", label: "Earnings Yield", hint: "Lucro por ação / preço do papel", group: "Valuation", format: pct },
  { key: "dividend_yield", label: "Dividend Yield", hint: "Proventos B3 por papel com data ex na janela / preço do próprio papel", group: "Valuation", format: pct },
  { key: "cfo_yield", label: "FCO Yield", hint: "Fluxo de caixa operacional / valor de mercado", group: "Valuation", format: pct },
  { key: "fcf_yield", label: "FCL Yield", hint: "Fluxo de caixa livre / valor de mercado", group: "Valuation", format: pct },
  { key: "payout_cash_paid_in_period", label: "Payout", hint: "Caixa pago pela companhia no período / lucro do mesmo período — sem atribuição ao exercício de origem", group: "Valuation", format: pct },
  { key: "eps_basic", label: "LPA", hint: "Lucro líquido do período / quantidade total de ações, com ajuste para units", group: "Valuation", format: price },
  { key: "bvps", label: "VPA", hint: "Valor patrimonial por ação (patrimônio / número de ações)", group: "Valuation", format: price },
  { key: "free_float", label: "Free Float", hint: "Percentual total de ações em circulação informado no FRE da companhia", group: "Valuation", format: pct },
  { key: "net_debt", label: "Dívida líquida", hint: "Dívida total − caixa e equivalentes classificados no CPC 03", group: "Endividamento", format: money },
  { key: "net_debt_to_ebitda", label: "Dívida líquida/EBITDA", hint: "Anos de EBITDA para quitar a dívida líquida", group: "Endividamento", format: multiple },
  { key: "net_debt_to_ebit", label: "Dívida líquida/EBIT", hint: "Anos de lucro operacional (EBIT) para quitar a dívida líquida", group: "Endividamento", format: multiple },
  { key: "net_debt_to_equity", label: "Dívida líquida/PL", hint: "Dívida líquida / patrimônio líquido — alavancagem líquida de caixa", group: "Endividamento", format: multiple },
  { key: "debt_to_equity", label: "Dívida bruta/PL", hint: "Dívida total / patrimônio líquido", group: "Endividamento", format: multiple },
  { key: "liabilities_to_assets", label: "Passivo / ativo", hint: "Passivo total / ativo total — fatia dos ativos financiada por capital de terceiros", group: "Endividamento", format: pct },
  { key: "equity_to_assets", label: "PL/Ativo", hint: "Patrimônio dos controladores / ativo total — não é o complemento do Passivo/Ativo: o que falta entre os dois é a fatia dos minoritários", group: "Endividamento", format: pct },
  { key: "current_ratio", label: "Liquidez corrente", hint: "Ativo circulante / passivo circulante", group: "Endividamento", format: multiple },
  { key: "cash_ratio", label: "Liquidez imediata", hint: "Caixa e equivalentes / passivo circulante", group: "Endividamento", format: multiple },
  { key: "quick_ratio", label: "Liquidez seca", hint: "(Ativo circulante − estoques) / passivo circulante", group: "Endividamento", format: multiple },
  { key: "gross_margin", label: "Margem bruta", hint: "Lucro bruto / receita", group: "Eficiência", format: pct },
  { key: "ebit_margin", label: "Margem EBIT", hint: "EBIT (lucro operacional) / receita", group: "Eficiência", format: pct },
  { key: "ebitda_margin", label: "Margem EBITDA", hint: "EBITDA / receita", group: "Eficiência", format: pct },
  { key: "net_margin", label: "Margem líquida", hint: "Lucro dos controladores / receita", group: "Eficiência", format: pct },
  { key: "asset_turnover", label: "Giro dos ativos", hint: "Receita / ativo total — quantas vezes os ativos geram vendas no ano", group: "Eficiência", format: multiple },
  { key: "cfo_margin", label: "Margem FCO", hint: "Fluxo de caixa operacional / receita", group: "Eficiência", format: pct },
  { key: "fcf_margin", label: "Margem FCL", hint: "Fluxo de caixa livre / receita", group: "Eficiência", format: pct },
  { key: "cash_conversion", label: "Conversão do lucro em caixa", hint: "Fluxo de caixa operacional / lucro líquido", group: "Eficiência", format: pct },
  { key: "capex_to_cfo", label: "CAPEX/FCO", hint: "Investimentos em imobilizado e intangíveis / fluxo de caixa operacional", group: "Eficiência", format: pct },
  { key: "fcf", label: "FCL", hint: "Caixa operacional − investimentos em ativos (CAPEX)", group: "Eficiência", format: money },
  { key: "roe", label: "ROE", hint: "Lucro dos controladores / patrimônio líquido dos controladores", group: "Rentabilidade", format: pct },
  { key: "roa", label: "ROA", hint: "Lucro dos controladores / ativo total", group: "Rentabilidade", format: pct },
  { key: "roic_statutory", label: "ROIC", hint: "EBIT consolidado após alíquota legal de 34% / capital investido consolidado", group: "Rentabilidade", format: pct },
  { key: "revenue_growth", label: "Crescimento da receita", hint: "Variação da receita frente ao ano anterior", group: "Crescimento", format: signedPct },
  { key: "net_income_growth", label: "Crescimento do lucro", hint: "Variação do lucro frente ao ano anterior", group: "Crescimento", format: signedPct },
  { key: "revenue_cagr_5y", label: "CAGR Receita (5 anos)", hint: "Crescimento anual composto da receita em 5 anos — extremos a 5 exercícios de distância", group: "Crescimento", format: signedPct },
  { key: "ebitda_cagr_5y", label: "CAGR EBITDA (5 anos)", hint: "Crescimento anual composto do EBITDA em 5 anos", group: "Crescimento", format: signedPct },
  { key: "ebit_cagr_5y", label: "CAGR EBIT (5 anos)", hint: "Crescimento anual composto do lucro operacional em 5 anos", group: "Crescimento", format: signedPct },
  { key: "net_income_cagr_5y", label: "CAGR Lucro (5 anos)", hint: "Crescimento anual composto do lucro líquido em 5 anos", group: "Crescimento", format: signedPct },
];

export const INDICATOR_GROUPS: IndicatorGroup[] = [
  "Valuation",
  "Endividamento",
  "Eficiência",
  "Rentabilidade",
  "Crescimento",
];

/**
 * One restrained hue per group, derived from the existing sector palette. The
 * colours are used as thin rules and headings; the values remain neutral.
 */
const GROUP_COLOR_VARS: Record<IndicatorGroup, string> = {
  Valuation: "--color-pastel-violet",
  Endividamento: "--color-pastel-rose",
  Eficiência: "--color-pastel-sky",
  Rentabilidade: "--color-pastel-mint",
  Crescimento: "--color-pastel-amber",
};

export function groupColor(group: IndicatorGroup): string {
  return `var(${GROUP_COLOR_VARS[group]})`;
}

/**
 * A formatter named rather than passed.
 *
 * `IndicatorChart` is a Client Component and `HistoryCharts` is a Server one,
 * and a function cannot cross that boundary — React has no way to serialize it.
 * So the chart takes the formatter's *name* and resolves it on its own side.
 */
export type FormatKind = "pct" | "signedPct" | "multiple" | "price" | "money";

export function formatKindOf(spec: IndicatorSpec): FormatKind {
  if (spec.format === money) return "money";
  if (spec.format === price) return "price";
  if (spec.format === multiple) return "multiple";
  if (spec.format === signedPct) return "signedPct";
  return "pct";
}

/**
 * Formatter for an axis tick: the currency prefix is dropped, since "R$ 15,00"
 * wraps onto two lines in a tick's width and the card's own label already says
 * what the unit is.
 *
 * Every branch wraps its formatter in a one-argument lambda **on purpose**.
 * Recharts calls `tickFormatter(value, index)`, and these formatters take
 * `(value, digits)` — handing them over bare makes the index land in `digits`,
 * so each tick down the axis grows a decimal place ("0%", "20,0%", "40,00%").
 */
export function axisFormatter(kind: FormatKind): (n: number) => string {
  switch (kind) {
    case "money":
      return (n) => money(n).replace("R$ ", "");
    case "price":
      return (n) => price(n).replace("R$ ", "");
    case "multiple":
      return (n) => multiple(n);
    case "signedPct":
      return (n) => signedPct(n);
    case "pct":
      return (n) => pct(n);
  }
}

/**
 * The change from a closed exercise to the current window, in the unit the
 * reader thinks in.
 *
 * A ratio already rendered as a percentage moves in **percentage points**: ROE
 * from 24% to 26% is `+2 p.p.`, not `+8%` — that would be the percent change of
 * a percent, a number nobody wants. A multiple moves in `×`. Money and
 * per-share figures have no natural unit of change, so they move in relative
 * terms.
 *
 * Returns null when the comparison is not a number: either side missing, or a
 * relative change measured against zero.
 */
export function deltaText(kind: FormatKind, current: number, previous: number): string | null {
  const raw = current - previous;
  const decimals = (n: number, digits: number) =>
    new Intl.NumberFormat("pt-BR", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(Math.abs(n));
  // A change that rounds away at the shown precision is not a change worth an
  // arrow: "▼ 0,00×" points somewhere and says nothing.
  const vanishes = (n: number, digits: number) => Number(Math.abs(n).toFixed(digits)) === 0;

  switch (kind) {
    case "pct":
    case "signedPct": {
      const points = raw * 100;
      if (vanishes(points, 1)) return null;
      return `${signOf(points)}${decimals(points, 1)} p.p.`;
    }
    case "multiple":
      if (vanishes(raw, 2)) return null;
      return `${signOf(raw)}${decimals(raw, 2)}×`;
    case "money":
    case "price": {
      if (previous === 0) return null;
      const ratio = (raw / Math.abs(previous)) * 100;
      if (vanishes(ratio, 1)) return null;
      return `${signOf(ratio)}${decimals(ratio, 1)}%`;
    }
  }
}

/**
 * The arrow, never a colour.
 *
 * Direction is a fact; "good" is a judgement the domain refuses to make, and a
 * rising P/L is not the same news as a rising ROE. Green here would quietly
 * decide that for the reader.
 */
function signOf(n: number): string {
  if (n > 0) return "▲ ";
  if (n < 0) return "▼ ";
  return "";
}

/** Formatter for a value read on its own — tooltip, stat tile — unit included. */
export function valueFormatter(kind: FormatKind): (n: number) => string {
  switch (kind) {
    case "money":
      return (n) => money(n);
    case "price":
      return (n) => price(n);
    case "multiple":
      return (n) => multiple(n);
    case "signedPct":
      return (n) => signedPct(n);
    case "pct":
      return (n) => pct(n);
  }
}

/**
 * Display metadata for the consolidated siblings.
 *
 * Deliberately **not** in `INDICATORS`: they are a second basis for an existing
 * cell, not four more cells. The grid keeps 29 cells; the drill-down resolves
 * these through `specByKey` when the reader switches basis.
 */
const TOTAL_SPECS: IndicatorSpec[] = [
  { key: "roe_total", label: "ROE", hint: "Retorno sobre o patrimônio líquido — base consolidada", group: "Rentabilidade", format: pct },
  { key: "roa_total", label: "ROA", hint: "Retorno sobre os ativos — base consolidada", group: "Rentabilidade", format: pct },
  { key: "net_margin_total", label: "Margem líquida", hint: "Lucro do grupo / receita — base consolidada", group: "Eficiência", format: pct },
  { key: "net_income_total", label: "Lucro líquido", hint: "Resultado do grupo, com minoritários — base consolidada", group: "Rentabilidade", format: money },
];

export function specsByGroup(group: IndicatorGroup): IndicatorSpec[] {
  return INDICATORS.filter((s) => s.group === group);
}

export function specByKey(key: IndicatorKey): IndicatorSpec | undefined {
  return INDICATORS.find((s) => s.key === key) ?? TOTAL_SPECS.find((s) => s.key === key);
}
