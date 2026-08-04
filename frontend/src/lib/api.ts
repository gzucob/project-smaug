/**
 * Typed client for the FastAPI read API (`smaug.entrypoints.api`).
 *
 * Reads run from Server Components, so those requests hit the API's own base
 * URL directly (no CORS concern — server to server). The favorite-ticker
 * toggle (#151) is the one *write*, and it is the one call that runs
 * client-side (a button click has nowhere else to originate from) — routed
 * through this app's own same-origin `/api/portfolio/[ticker]` Route Handler
 * rather than at the FastAPI base URL directly, so the browser itself never
 * gains a cross-origin surface (`RULES_FRONTEND`: reads stay server-side;
 * ADR 0049 covers the backend's matching CORS allowance for that one proxy).
 *
 * Every call returns an `ApiResult` rather than throwing, so pages can render
 * a friendly vault-offline state when the backend isn't running.
 */
import type { Analysis, PortfolioTicker, TickerViews } from "@/lib/types";

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

/** Latest analysis for every ticker that has one. */
export function fetchPortfolio(): Promise<ApiResult<Analysis[]>> {
  return get<Analysis[]>("/analysis");
}

/** Both perspectives (live TTM + closed-year history) for one ticker. */
export function fetchTicker(symbol: string): Promise<ApiResult<TickerViews>> {
  return get<TickerViews>(`/analysis/${encodeURIComponent(symbol.toUpperCase())}`);
}

/** Every ticker the user has favorited (#151) — membership, not analysis. */
export function fetchPortfolioList(): Promise<ApiResult<PortfolioTicker[]>> {
  return get<PortfolioTicker[]>("/portfolio");
}

async function mutatePortfolio(
  ticker: string,
  method: "POST" | "DELETE",
): Promise<ApiResult<null>> {
  try {
    // Same-origin: this app's own proxy route, never the FastAPI base URL —
    // see the module docstring.
    const res = await fetch(`/api/portfolio/${encodeURIComponent(ticker.toUpperCase())}`, {
      method,
      cache: "no-store",
    });
    if (!res.ok) {
      return { ok: false, status: res.status, message: `A API respondeu ${res.status}.` };
    }
    return { ok: true, data: null };
  } catch {
    return { ok: false, status: 0, message: "Não foi possível favoritar agora." };
  }
}

/** Favorite a ticker. Idempotent — safe to call on one already favorited. */
export function addFavorite(ticker: string): Promise<ApiResult<null>> {
  return mutatePortfolio(ticker, "POST");
}

/** Un-favorite a ticker. Idempotent — safe to call on one not favorited. */
export function removeFavorite(ticker: string): Promise<ApiResult<null>> {
  return mutatePortfolio(ticker, "DELETE");
}

export const API_BASE = BASE;
