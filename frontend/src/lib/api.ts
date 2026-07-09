/**
 * Typed client for the FastAPI read API (`smaug.entrypoints.api`).
 *
 * Used from Server Components, so requests run server-side (no CORS concern).
 * Every call returns an `ApiResult` rather than throwing, so pages can render a
 * friendly vault-offline state when the backend isn't running.
 */
import type { Analysis, TickerViews } from "@/lib/types";

const BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; message: string };

async function get<T>(path: string): Promise<ApiResult<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
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
      message: "Não foi possível falar com a API. O cofre está fechado (backend offline?).",
    };
  }
}

/** Latest analysis for every ticker that has one — powers the portfolio view. */
export function fetchPortfolio(): Promise<ApiResult<Analysis[]>> {
  return get<Analysis[]>("/analysis");
}

/** Both perspectives (live TTM + closed-year history) for one ticker. */
export function fetchTicker(symbol: string): Promise<ApiResult<TickerViews>> {
  return get<TickerViews>(`/analysis/${encodeURIComponent(symbol.toUpperCase())}`);
}

export const API_BASE = BASE;
