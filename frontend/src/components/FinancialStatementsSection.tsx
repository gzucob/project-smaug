import { CashFlowStatementTable } from "@/components/CashFlowStatementTable";
import { DividendHistoryPanel } from "@/components/DividendHistoryPanel";
import { IncomeStatementTable } from "@/components/IncomeStatementTable";
import type { Analysis, CashDividendHistory } from "@/lib/types";

export function FinancialStatementsSection({
  history,
  ttm,
  cashHistory,
}: {
  history: Analysis[];
  ttm: Analysis | null;
  cashHistory: Promise<CashDividendHistory | null>;
}) {
  return (
    <div
      id="demonstrativos"
      aria-label="Demonstrativos financeiros"
      className="scroll-mt-32"
    >
      <div className="flex flex-col gap-10">
        <IncomeStatementTable history={history} ttm={ttm} />
        <CashFlowStatementTable history={history} />
        <Suspense fallback={<SectionPlaceholder label="Proventos" tall />}>
          <CashDividends
            history={history}
            ttm={ttm}
            cashHistory={cashHistory}
          />
        </Suspense>
      </div>
    </div>
  );
}
import { Suspense } from "react";
import { SectionPlaceholder } from "@/components/SectionPlaceholder";

async function CashDividends({
  history,
  ttm,
  cashHistory,
}: {
  history: Analysis[];
  ttm: Analysis | null;
  cashHistory: Promise<CashDividendHistory | null>;
}) {
  return (
    <DividendHistoryPanel
      history={history}
      ttm={ttm}
      cashHistory={await cashHistory}
    />
  );
}
