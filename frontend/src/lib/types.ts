/**
 * TypeScript mirror of the FastAPI read-API response models
 * (`smaug.entrypoints.api`). Decimals may arrive as a JSON number or a string
 * depending on Pydantic's serialization, so numeric fields are `Decimalish`
 * and always coerced through `toNum()` in the presentation layer.
 */

export type Decimalish = number | string | null;

export type SectorKey =
  | "bank"
  | "insurer"
  | "utility"
  | "commodity"
  | "industry";

export type ViewKind = "ttm_live" | "closed_year";

/**
 * B3 economic taxonomy (setor → subsetor → segmento), mirroring the API's
 * `ClassificationResponse`. `subsetor`/`segmento` are null under the CVM
 * single-level fallback for a ticker outside the snapshot (ADR 0024).
 */
export interface Classification {
  setor: string;
  subsetor: string | null;
  segmento: string | null;
}

export interface Indicators {
  // The whole-firm ratios come on both statement slices (ADR 0026): the bare
  // name pairs the controllers' result with the controllers' equity, and the
  // `_total` variant pairs the consolidated total (minoritários included) —
  // the basis the reference platforms publish for margins and ROE.
  roe: Decimalish;
  roe_total: Decimalish;
  roa: Decimalish;
  roa_total: Decimalish;
  roic: Decimalish;
  net_margin: Decimalish;
  net_margin_total: Decimalish;
  gross_margin: Decimalish;
  ebit_margin: Decimalish;
  ebitda_margin: Decimalish;
  asset_turnover: Decimalish;
  eps: Decimalish;
  bvps: Decimalish;
  net_debt: Decimalish;
  net_debt_to_ebitda: Decimalish;
  debt_to_equity: Decimalish;
  liabilities_to_assets: Decimalish;
  current_ratio: Decimalish;
  revenue_growth: Decimalish;
  net_income_growth: Decimalish;
  pe: Decimalish;
  pb: Decimalish;
  psr: Decimalish;
  price_to_assets: Decimalish;
  price_to_ebit: Decimalish;
  price_to_working_capital: Decimalish;
  payout: Decimalish;
  dividend_yield: Decimalish;
  ev_ebitda: Decimalish;
  fcf: Decimalish;
  price_to_fcf: Decimalish;
  fcf_yield: Decimalish;
  // Bank-only (ADR 0021): null under every other accounting regime.
  net_interest_margin: Decimalish;
  efficiency_ratio: Decimalish;
  cost_of_risk: Decimalish;
  revenue: Decimalish;
  net_income: Decimalish;
  net_income_total: Decimalish;
  dividends: Decimalish;
  market_cap: Decimalish;
  enterprise_value: Decimalish;
  shares: Decimalish;
}

export type IndicatorKey = keyof Indicators;

export interface Analysis {
  ticker: string;
  view: ViewKind | string;
  classification: Classification;
  reference_date: string; // ISO date
  computed_at: string; // ISO datetime
  price: Decimalish;
  price_adjusted: Decimalish; // total-return basis; null on the live view
  price_basis: string | null;
  indicators: Indicators;
}

export interface TickerViews {
  ticker: string;
  ttm: Analysis | null;
  history: Analysis[]; // closed years, oldest → newest
}
