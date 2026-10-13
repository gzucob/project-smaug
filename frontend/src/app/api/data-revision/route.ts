import { NextResponse } from "next/server";
import { fetchDataRevision } from "@/lib/api";

/** Keep revision probes on the same origin as the rest of the UI. */
export async function GET() {
  const result = await fetchDataRevision();
  return NextResponse.json(result.ok ? result.data : { revision: null }, {
    status: result.ok ? 200 : 503,
    headers: { "Cache-Control": "no-store" },
  });
}
