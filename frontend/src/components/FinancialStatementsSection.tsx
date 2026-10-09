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
  cashHistory: CashDividendHistory | null;
}) {
  return (
    <div id="demonstrativos" aria-label="Demonstrativos financeiros" className="scroll-mt-32">
      <div className="flex flex-col gap-10">
        <IncomeStatementTable history={history} ttm={ttm} />
        <CashFlowStatementTable history={history} />
        <DividendHistoryPanel
          history={history}
          ttm={ttm}
          cashHistory={cashHistory}
        />
      </div>
    </div>
  );
}
