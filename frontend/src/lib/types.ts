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

export interface Indicators {
  roe: Decimalish;
  roa: Decimalish;
  roic: Decimalish;
  net_margin: Decimalish;
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
  revenue: Decimalish;
  net_income: Decimalish;
  dividends: Decimalish;
}

export type IndicatorKey = keyof Indicators;

export interface Analysis {
  ticker: string;
  view: ViewKind | string;
  sector: SectorKey | string;
  reference_date: string; // ISO date
  computed_at: string; // ISO datetime
  price: Decimalish;
  price_nominal: Decimalish;
  price_basis: string | null;
  indicators: Indicators;
}

export interface TickerViews {
  ticker: string;
  ttm: Analysis | null;
  history: Analysis[]; // closed years, oldest → newest
}
