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

/** One code filed for a share class and the years in which CVM named it. */
export interface TickerCodeEvidence {
  symbol: string;
  filed_years: number[];
  source: string;
}

/** Share-class identity and the evidence used to resolve it. */
export interface ShareClassMapping {
  class_id: string;
  symbol: string | null;
  kind: string | null;
  per_share_class: string | null;
  status: string;
  economic_rights: string;
  resolution_reason: string | null;
  code_evidence: TickerCodeEvidence[];
  evidence: string[];
}

/** One listed class contribution to the company's market value. */
export interface ClassMarketValue {
  class_id: string;
  symbol: string;
  per_share_class: string;
  price: Decimalish;
  shares: Decimalish;
  value: Decimalish;
  price_basis: string;
  share_basis: string;
  null_reason: NullReason | null;
}

export interface ShareCounts {
  common: Decimalish;
  preferred: Decimalish;
  total: Decimalish;
  preferred_a: Decimalish;
  preferred_b: Decimalish;
  preferred_other: Decimalish;
}

export interface CapitalComposition {
  issued_total: Decimalish;
  treasury_common: Decimalish;
  treasury_preferred: Decimalish;
  treasury_total: Decimalish;
}

export interface CapitalAction {
  approval_date: string;
  kind: string;
  common_before: Decimalish;
  common_after: Decimalish;
  preferred_before: Decimalish;
  preferred_after: Decimalish;
  total_before: Decimalish;
  total_after: Decimalish;
}

/** Audit trail behind the outstanding-share count used by the analysis. */
export interface ShareCountProvenance {
  requested_year: number;
  filed_year: number | null;
  status: string;
  source: string;
  issued: ShareCounts | null;
  outstanding: ShareCounts | null;
  treasury: CapitalComposition | null;
  restatement_factor: Decimalish;
  actions: CapitalAction[];
  evidence: string[];
}

export interface SourceAccountRef {
  code: string;
  name: string;
  value: Decimalish;
  column: string | null;
}

/** Mapping/absence evidence for one calculator input. */
export interface SourceAccountEvidence {
  field: string;
  statement: string;
  status: string;
  expected: string[];
  found: SourceAccountRef[];
  parent_code: string | null;
  formula: string | null;
  dependencies: string[];
  blocker: NullReason | string | null;
  consumer_indicators: string[];
  duplicates_discarded: number;
}

export interface Cpc41AccountEvidence {
  module: string;
  code: string;
  name: string;
  selection_status: string;
  value: Decimalish;
  basis: string | null;
  expected: boolean;
}

export interface Cpc41PeriodProvenance {
  reference_date: string;
  disclosure_status: string;
  class_status: string;
  multiplier_status: string;
  multiplier: Decimalish;
  basic_weighted_shares: Decimalish;
  basic_weighted_shares_status: string;
  diluted_weighted_shares: Decimalish;
  diluted_weighted_shares_status: string;
  basic_blocker: NullReason | null;
  diluted_blocker: NullReason | null;
  source_accounts: Cpc41AccountEvidence[];
  basic_disclosure_status?: string;
  diluted_disclosure_status?: string;
  basic_class_status?: string;
  diluted_class_status?: string;
  basic_multiplier_status?: string;
  diluted_multiplier_status?: string;
}

export interface Cpc41WindowProvenance {
  selected_periods: Cpc41PeriodProvenance[];
  basic_blocker: NullReason | null;
  diluted_blocker: NullReason | null;
}


export interface DebtLine {
  code: string;
  name: string;
  value: Decimalish;
  role: string;
  reason: string | null;
  instrument: string;
  classification: string;
}

export interface DebtEvidence {
  regime: string;
  regime_source: string;
  identity_status: string;
  used_lines: DebtLine[];
  excluded_lines: DebtLine[];
  included_instruments: string[];
  primary_blocker: string | null;
  secondary_blockers: string[];
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
  roic_statutory: Decimalish;
  net_margin: Decimalish;
  net_margin_total: Decimalish;
  gross_margin: Decimalish;
  ebit_margin: Decimalish;
  ebitda_margin: Decimalish;
  asset_turnover: Decimalish;
  eps_basic: Decimalish;
  eps_diluted: Decimalish;
  bvps: Decimalish;
  net_debt: Decimalish;
  cash_equivalents: Decimalish;
  current_financial_investments: Decimalish;
  net_debt_to_ebitda: Decimalish;
  net_debt_to_ebit: Decimalish;
  net_debt_to_equity: Decimalish;
  debt_to_equity: Decimalish;
  liabilities_to_assets: Decimalish;
  equity_to_assets: Decimalish;
  current_ratio: Decimalish;
  price_to_cfo: Decimalish;
  ev_cfo: Decimalish;
  ev_fcf: Decimalish;
  cash_ratio: Decimalish;
  quick_ratio: Decimalish;
  ev_revenue: Decimalish;
  tag_along: Decimalish;
  free_float: Decimalish;
  price_to_ebitda: Decimalish;
  cfo_yield: Decimalish;
  cfo_margin: Decimalish;
  fcf_margin: Decimalish;
  cash_conversion: Decimalish;
  capex_to_cfo: Decimalish;
  revenue_growth: Decimalish;
  net_income_growth: Decimalish;
  // Compounded annual growth over a stated window (#144): the endpoints sit five
  // closed exercises apart, so six are needed and a shorter history is null.
  revenue_cagr_5y: Decimalish;
  ebitda_cagr_5y: Decimalish;
  ebit_cagr_5y: Decimalish;
  net_income_cagr_5y: Decimalish;
  earnings_yield: Decimalish;
  pe_basic: Decimalish;
  pe_diluted: Decimalish;
  pb: Decimalish;
  psr: Decimalish;
  price_to_assets: Decimalish;
  price_to_ebit: Decimalish;
  price_to_working_capital: Decimalish;
  dividend_yield: Decimalish;
  payout_cash_paid_in_period: Decimalish;
  ev_ebitda: Decimalish;
  ev_ebit: Decimalish;
  fcf: Decimalish;
  price_to_fcf: Decimalish;
  fcf_yield: Decimalish;
  // Bank-only (ADR 0058): null under every other accounting regime.
  // Insurance-only underwriting ratios (ADR 0061).
  revenue: Decimalish;
  net_income: Decimalish;
  net_income_total: Decimalish;
  distributions_per_security: Decimalish;
  company_distributions_paid_in_period: Decimalish;
  // Balance-sheet scale in absolute reais (#142) — the ratios divide these away,
  // so a chart of the two sides of the balance sheet needs the sides themselves.
  total_assets: Decimalish;
  total_liabilities: Decimalish;
  equity: Decimalish;
  equity_total: Decimalish;
  market_cap: Decimalish;
  enterprise_value: Decimalish;
  non_controlling_interests: Decimalish;
  shares: Decimalish;
  // Why each null is null (ADR 0008). A key absent from the map is a null with
  // no recorded cause — "unclassified", a reportable status of its own (#47).
  null_reasons: Partial<Record<string, NullReason>>;
  /** Raw-account lineage retained by the API for audit and explanation. */
  source_account_evidence?: SourceAccountEvidence[];
  /** Filing diagnostics retained by the read API; not displayed in the UI. */
  cpc41_window_provenance?: Cpc41WindowProvenance | null;
  /** Regulatory inputs behind bank-only indicators, when available. */
}

/** Formula metadata published by the API for market-facing indicators. */
export interface IndicatorContract {
  basis: string;
  numerator: string;
  denominator: string;
  reference_period: string;
  /** Points to the view-level price basis carried by `Analysis`. */
  price_basis: string;
  /** Describes the share denominator selected for this calculation version. */
  share_basis: string;
  provenance: string[];
}

/**
 * The calculator's enumerable causes for a null (`NullReason` in
 * `analysis/domain/indicators.py`). The front-end never infers these: it used
 * to mirror the sector guards by hand, which is the duplication #30 flagged
 * and #54 removed.
 */
export type NullReason =
  | "inapplicable_regime"
  | "current_only_indicator"
  | "source_account_unmapped"
  | "source_account_absent"
  | "missing_price"
  | "price_symbol_not_found"
  | "not_yet_listed"
  | "missing_share_count"
  | "missing_unit_composition"
  | "missing_regulatory_disclosure"
  | "missing_tag_along_evidence"
  | "unresolved_tag_along_classes"
  | "conflicting_tag_along_evidence"
  | "incomplete_debt_coverage"
  | "missing_cpc41_disclosure"
  | "missing_weighted_average_shares"
  | "missing_economic_rights"
  | "missing_cash_distributions"
  | "missing_cash_distribution_value"
  | "missing_prior_period"
  | "zero_denominator"
  | "non_positive_endpoint";

/** Indicator fields displayed in the UI; the API mirror retains hidden fields. */
export type IndicatorKey = Exclude<
  keyof Indicators,
  "null_reasons" | "source_account_evidence" | "cpc41_window_provenance"
  | "eps_diluted" | "pe_diluted"
>;

export interface Analysis {
  calculation_contract_version: string;
  ticker: string;
  view: ViewKind | string;
  classification: Classification;
  governance?: {
    ipo_date: string | null;
    listing_segment: string | null;
    listing_observed_on: string | null;
    listing_source: string | null;
    tag_along_source: string | null;
    tag_along_reference: string | null;
    blocker: string | null;
  };
  reference_date: string; // ISO date
  computed_at: string; // ISO datetime
  filed_regime?: string | null;
  regime_source?: string | null;
  issuer?: string | null;
  cd_cvm?: string | null;
  cnpj?: string | null;
  price: Decimalish;
  price_source_code?: string | null;
  price_source_session?: string | null;
  price_adjusted: Decimalish; // total-return basis; null on the live view
  price_basis: string | null;
  share_count_basis: string | null;
  liquidity_basis: string | null;
  debt_basis: string | null;
  debt_evidence_snapshot?: string | null;
  debt_evidence?: DebtEvidence | null;
  roic_tax_basis: string | null;
  share_class_mappings?: ShareClassMapping[];
  class_market_values?: ClassMarketValue[];
  capital_provenance?: ShareCountProvenance | null;
  indicators: Indicators;
  /** Present for market-facing indicators; older API versions may omit it. */
  indicator_contract?: Partial<Record<IndicatorKey, IndicatorContract>>;
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
export interface PriceHistory {
  ticker: string;
  computed_at: string;
  start_year: number;
  end_year: number;
  contract_version: string;
  price_basis: "split_adjusted";
  source: string;
  points: {
    session: string;
    code: string;
    as_traded: Decimalish;
    adjusted: Decimalish;
    factor: Decimalish;
  }[];
  gaps: { year: number; reason: string }[];
}
