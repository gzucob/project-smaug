import type { ApiResult } from "@/lib/api-result";

/**
 * Same-origin client for the favorite toggle. This is one of two browser
 * requests permitted by the frontend data boundary: the Next.js route proxies
 * it to FastAPI server-side. Keep all analysis reads in Server Components via
 * `api.ts`.
 */
async function mutatePortfolio(
  ticker: string,
  method: "POST" | "DELETE",
): Promise<ApiResult<null>> {
  try {
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
