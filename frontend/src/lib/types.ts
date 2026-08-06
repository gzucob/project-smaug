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
  // `_total` variant pairs the consolidated total (minoritários included) with
  // its consolidated denominator.
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
  net_debt_to_ebit: Decimalish;
  net_debt_to_equity: Decimalish;
  debt_to_equity: Decimalish;
  liabilities_to_assets: Decimalish;
  equity_to_assets: Decimalish;
  current_ratio: Decimalish;
  revenue_growth: Decimalish;
  net_income_growth: Decimalish;
  // Compounded annual growth over a stated window (#144): the endpoints sit five
  // closed exercises apart, so six are needed and a shorter history is null.
  revenue_cagr_5y: Decimalish;
  ebitda_cagr_5y: Decimalish;
  ebit_cagr_5y: Decimalish;
  net_income_cagr_5y: Decimalish;
  pe: Decimalish;
  pb: Decimalish;
  psr: Decimalish;
  price_to_assets: Decimalish;
  price_to_ebit: Decimalish;
  price_to_working_capital: Decimalish;
  payout: Decimalish;
  dividend_yield: Decimalish;
  // Declared basis (#104): the DMPL equity charge, not the DFC cash outflow.
  payout_declared: Decimalish;
  dividend_yield_declared: Decimalish;
  ev_ebitda: Decimalish;
  ev_ebit: Decimalish;
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
  dividends_declared: Decimalish;
  // Balance-sheet scale in absolute reais (#142) — the ratios divide these away,
  // so a chart of the two sides of the balance sheet needs the sides themselves.
  total_assets: Decimalish;
  total_liabilities: Decimalish;
  equity: Decimalish;
  equity_total: Decimalish;
  market_cap: Decimalish;
  enterprise_value: Decimalish;
  shares: Decimalish;
  // Why each null is null (ADR 0008). A key absent from the map is a null with
  // no recorded cause — "unclassified", a reportable status of its own (#47).
  null_reasons: Partial<Record<string, NullReason>>;
}

/**
 * The calculator's enumerable causes for a null (`NullReason` in
 * `analysis/domain/indicators.py`). The front-end never infers these: it used
 * to mirror the sector guards by hand, which is the duplication #30 flagged
 * and #54 removed.
 */
export type NullReason =
  | "inapplicable_regime"
  | "source_account_unmapped"
  | "source_account_absent"
  | "missing_price"
  | "price_symbol_not_found"
  | "not_yet_listed"
  | "missing_share_count"
  | "missing_prior_period"
  | "zero_denominator"
  | "non_positive_endpoint";

/** Every indicator field — `null_reasons` is metadata about them, not one of them. */
export type IndicatorKey = Exclude<keyof Indicators, "null_reasons">;

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

/** One favorited ticker (#151), mirroring `PortfolioTickerResponse`. */
export interface PortfolioTicker {
  ticker: string;
  added_at: string; // ISO datetime
}
