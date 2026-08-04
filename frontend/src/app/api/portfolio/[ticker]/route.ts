/**
 * Same-origin proxy for the favorite-ticker toggle (#151).
 *
 * The only reason this route exists: `FavoriteButton` runs client-side (a
 * click has nowhere else to originate from), and every other call this app
 * makes is a server-side fetch straight to the FastAPI base URL. Routing the
 * one write through this app's own origin instead of the browser calling
 * FastAPI directly is what keeps the browser's cross-origin surface at zero —
 * only this Next.js server ever crosses to FastAPI, which is exactly the one
 * caller ADR 0049's `CORSMiddleware` allowance was scoped for.
 */
import { NextResponse } from "next/server";
import { API_BASE } from "@/lib/api";

async function proxy(ticker: string, method: "POST" | "DELETE") {
  const res = await fetch(`${API_BASE}/portfolio/${encodeURIComponent(ticker.toUpperCase())}`, {
    method,
    cache: "no-store",
  });
  if (res.status === 204) return new NextResponse(null, { status: 204 });
  const body = await res.text();
  return new NextResponse(body, {
    status: res.status,
    headers: { "content-type": "application/json" },
  });
}

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;
  return proxy(ticker, "POST");
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;
  return proxy(ticker, "DELETE");
}
