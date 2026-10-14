import { Suspense } from "react";
import { fetchAnalysisSummaries } from "@/lib/api";
import { TickerSearchInput } from "@/components/TickerSearchInput";

export function TickerSearch({ compact = false }: { compact?: boolean }) {
  return (
    <Suspense fallback={<TickerSearchInput compact={compact} options={[]} loading />}>
      <LoadedTickerSearch compact={compact} />
    </Suspense>
  );
}

async function LoadedTickerSearch({ compact }: { compact: boolean }) {
  const result = await fetchAnalysisSummaries();
  const options = result.ok
    ? result.data.map(({ ticker, issuer }) => ({ ticker, issuer }))
    : [];

  return <TickerSearchInput compact={compact} options={options} unavailable={!result.ok} />;
}
