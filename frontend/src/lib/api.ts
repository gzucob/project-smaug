/**
 * Typed client for the FastAPI read API (`smaug.entrypoints.api`).
 *
 * This module is for Server Components and server-only routes. Client
 * components must not import it or call the FastAPI API directly; see the
 * data boundary in `frontend/AGENTS.md`. The favorite toggle uses the separate
 * same-origin proxy client in `favorites-api.ts`.
 *
 * Every read returns an `ApiResult` rather than throwing, so pages can render
 * a clear offline state when the backend isn't running.
 */
import { cache } from "react";
import type {
  Analysis,
  AnalysisSummary,
  DataRevision,
  CashDividendHistory,
  PortfolioTicker,
  PriceHistory,
  ShareholdingPosition,
  TickerViews,
} from "@/lib/types";
import type { ApiResult } from "@/lib/api-result";

export type { ApiResult } from "@/lib/api-result";

const BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");

async function get<T>(path: string, timeoutMs = 10000): Promise<ApiResult<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        message:
          res.status === 404
            ? "Nenhuma análise encontrada."
            : `A API respondeu ${res.status}.`,
      };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return {
      ok: false,
      status: 0,
      message: "Não foi possível falar com a API. O backend está offline?",
    };
  }
}

/** Latest analysis for every ticker that has one. */
export function fetchPortfolio(): Promise<ApiResult<Analysis[]>> {
  return get<Analysis[]>("/analysis");
}

/** Deduplicate the home sections within one render, keeping saved data fresh. */
export const fetchAnalysisSummaries = cache(() =>
  get<AnalysisSummary[]>("/analysis/summary"),
);

/** Small token for committed data changes, also proxied through our own origin. */
export const fetchDataRevision = cache(() => get<DataRevision>("/data/revision", 2000));

/** Both perspectives (current rolling period + closed-year history) for one ticker. */
export function fetchTicker(symbol: string): Promise<ApiResult<TickerViews>> {
  return get<TickerViews>(`/analysis/${encodeURIComponent(symbol.toUpperCase())}`);
}

/** B3 cash-event history for the ticker's resolved share class. */
export function fetchDividendHistory(
  symbol: string,
): Promise<ApiResult<CashDividendHistory>> {
  return get<CashDividendHistory>(
    `/dividends/${encodeURIComponent(symbol.toUpperCase())}/history`,
  );
}

/** Direct shareholder positions from the latest mirrored CVM FRE. */
export function fetchShareholdingPosition(
  symbol: string,
): Promise<ApiResult<ShareholdingPosition>> {
  return get<ShareholdingPosition>(
    `/shareholders/${encodeURIComponent(symbol.toUpperCase())}/position`,
  );
}

/** Daily closing history prepared by the CLI, fetched only on the server. */
export function fetchPriceHistory(symbol: string): Promise<ApiResult<PriceHistory>> {
  return get<PriceHistory>(`/prices/${encodeURIComponent(symbol.toUpperCase())}/history`);
}

/** Every ticker the user has favorited (#151) — membership, not analysis. */
export const fetchPortfolioList = cache(() => get<PortfolioTicker[]>("/portfolio"));

export const API_BASE = BASE;
