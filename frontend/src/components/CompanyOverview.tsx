import type { ReactNode } from "react";
import type { IconType } from "react-icons";
import { FiBriefcase, FiPieChart } from "react-icons/fi";
import { count, dateOnly, money, multiple, pct } from "@/lib/format";
import { listingSegmentLabel } from "@/lib/governance";
import type { Analysis } from "@/lib/types";

/** Company facts and financial highlights share a reference, but have distinct roles. */
export function CompanyOverview({ analysis }: { analysis: Analysis }) {
  const { indicators, governance } = analysis;
  const current = analysis.view === "ttm_live";

  return (
    <div className="grid items-start gap-8 xl:grid-cols-2">
      <OverviewPanel title="Dados da empresa" icon={FiBriefcase}>
        <dl className="divide-y divide-copy-200/5">
          <OverviewRow label="CNPJ" value={analysis.cnpj ?? "Não informado"} />
          <OverviewRow label="Data do IPO" value={dateOnly(governance?.ipo_date)} />
          <OverviewRow label="Valor de mercado" value={money(indicators.market_cap)} />
          <OverviewRow label="Quantidade de ações" value={count(indicators.shares)} />
          <OverviewRow label="Segmento de listagem" value={listingSegmentLabel(governance?.listing_segment)} numeric={false} />
          {current && (
            <OverviewRow label="Tag Along" value={pct(indicators.tag_along)} />
          )}
          <OverviewRow label="Free Float" value={pct(indicators.free_float)} />
        </dl>
      </OverviewPanel>

      <OverviewPanel title="Resumo financeiro" icon={FiPieChart} href="#indicadores" linkLabel="Ver indicadores">
        <dl className="divide-y divide-copy-200/5">
          <OverviewRow label="P/L" value={multiple(indicators.pe_basic)} />
          <OverviewRow label="P/VP" value={multiple(indicators.pb)} />
          <OverviewRow label="ROE" value={pct(indicators.roe)} />
          <OverviewRow label="Dividend Yield" value={pct(indicators.dividend_yield)} />
          <OverviewRow label="Valor da firma (EV)" value={money(indicators.enterprise_value)} />
          <OverviewRow label="Caixa e equivalentes" value={money(indicators.cash_equivalents)} />
          <OverviewRow label="Aplicações financeiras" value={money(indicators.current_financial_investments)} />
        </dl>
      </OverviewPanel>
    </div>
  );
}

function OverviewPanel({ title, icon: Icon, href, linkLabel, children }: { title: string; icon: IconType; href?: string; linkLabel?: string; children: ReactNode }) {
  return (
    <section className="min-w-0 rounded-lg border border-copy-200/10 bg-canvas-900 p-4 sm:p-5" aria-label={title}>
      <header className="mb-2 flex items-center justify-between gap-3 border-b border-copy-200/10 pb-4">
        <div className="flex min-w-0 items-center gap-2">
          <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-canvas-800 text-accent-300" aria-hidden>
            <Icon size={16} />
          </span>
          <h3 className="card-title">{title}</h3>
        </div>
        {href && linkLabel ? (
          <a href={href} className="pressable shrink-0 rounded text-xs font-medium text-accent-300 hover:text-accent-400 focus-visible:outline-2 focus-visible:outline-accent-400">
            {linkLabel}
          </a>
        ) : null}
      </header>
      {children}
    </section>
  );
}

function OverviewRow({ label, value, numeric = true }: {
  label: string;
  value: string;
  numeric?: boolean;
}) {
  return (
    <div className="py-3">
      <div className="flex items-start justify-between gap-4">
        <dt className="min-w-0 text-xs leading-relaxed text-copy-400">{label}</dt>
        <dd className={`max-w-[60%] text-right text-sm font-semibold leading-relaxed text-copy-100 ${numeric ? "nums" : ""}`}>{value}</dd>
      </div>
    </div>
  );
}
