import {
  FiCheckCircle,
  FiChevronDown,
  FiDatabase,
  FiGitBranch,
  FiInfo,
  FiLayers,
  FiShield,
} from "react-icons/fi";
import { count, dateTime, money, price, toNum } from "@/lib/format";
import type {
  Analysis,
  CapitalAction,
  ClassMarketValue,
  DebtEvidence,
  ShareCountProvenance,
  SourceAccountEvidence,
} from "@/lib/types";

/**
 * The API keeps a large amount of lineage beside the computed indicators. This
 * component gives that lineage a readable home without turning the default
 * analysis into a raw JSON dump. The detailed evidence stays behind a native
 * disclosure control, so it remains accessible without another client state
 * layer or a new dependency.
 */
const HIDDEN_PER_SHARE_EVIDENCE = /^(eps_diluted|pe_diluted)(\[|$)/;

export function AnalysisProvenance({ analysis }: { analysis: Analysis }) {
  const evidence = (analysis.indicators.source_account_evidence ?? []).filter(
    (item) => !HIDDEN_PER_SHARE_EVIDENCE.test(item.field),
  );
  const contracts = Object.entries(analysis.indicator_contract ?? {}).filter(
    ([key]) => key !== "eps_diluted" && key !== "pe_diluted",
  );
  const mapped = evidence.filter((item) => item.status === "mapped").length;
  const derived = evidence.filter((item) => item.status === "derived").length;
  const gaps = evidence.filter((item) => item.status === "absent" || item.status === "unmapped" || item.status === "present_unreadable").length;

  return (
    <section id="fontes" className="scroll-mt-24 py-8" aria-labelledby="provenance-title">
      <SectionHeading />

      <div className="mt-5 grid gap-px overflow-hidden rounded-lg border border-copy-200/10 bg-copy-200/10 sm:grid-cols-2 xl:grid-cols-4">
        <Fact
          label="Empresa"
          value={analysis.issuer ?? analysis.ticker}
          detail={[analysis.cnpj, analysis.cd_cvm ? `CVM ${analysis.cd_cvm}` : null]
            .filter(Boolean)
            .join(" · ")}
        />
        <Fact
          label="Classificação"
          value={analysis.classification.setor}
          detail={[analysis.classification.subsetor, analysis.classification.segmento]
            .filter(Boolean)
            .join(" · ")}
        />
        <Fact
          label="Regime informado"
          value={labelOf(analysis.filed_regime)}
          detail={analysis.regime_source ? labelOf(analysis.regime_source) : "não informado"}
        />
        <Fact
          label="Preço observado"
          value={price(analysis.price)}
          detail={[
            analysis.price_source_code ? `B3 ${analysis.price_source_code}` : null,
            analysis.price_source_session ?? null,
          ]
            .filter(Boolean)
            .join(" · ")}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <BasesPanel analysis={analysis} />
        <CapitalPanel
          analysis={analysis}
          marketValues={analysis.class_market_values ?? []}
          capital={analysis.capital_provenance ?? null}
        />
      </div>

      <details className="group mt-4 rounded-lg border border-copy-200/10 bg-canvas-900">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 text-sm font-semibold text-copy-200 marker:hidden focus-visible:outline-1 focus-visible:outline-accent-400 [&::-webkit-details-marker]:hidden">
          <span className="flex items-center gap-2.5">
            <FiDatabase aria-hidden size={15} className="text-accent-400" />
            Rastreabilidade completa
            <span className="text-xs font-normal text-copy-600">
              {mapped + derived} entradas reconhecidas
              {gaps > 0 ? ` · ${gaps} com lacuna nomeada` : ""}
            </span>
          </span>
          <FiChevronDown
            aria-hidden
            size={16}
            className="text-copy-500 transition-transform group-open:rotate-180"
          />
        </summary>

        <div className="border-t border-copy-200/10 px-5 py-5">
          <div className="grid gap-4 lg:grid-cols-2">
            <EvidencePanel evidence={evidence} />
            <ContractPanel count={contracts.length} />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <CapitalEvidence capital={analysis.capital_provenance ?? null} />
            <DebtEvidencePanel evidence={analysis.debt_evidence ?? null} />
          </div>


        </div>
      </details>
    </section>
  );
}

function SectionHeading() {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-1 h-8 w-1 shrink-0 rounded-full bg-accent-400" aria-hidden />
      <div>
        <p className="text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-accent-300">
          Transparência
        </p>
        <h2 id="provenance-title" className="mt-1 text-xl font-semibold tracking-tight text-copy-100">
          Dados e origem da análise
        </h2>
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-copy-600">
          O painel mostra de onde vêm os números, qual base foi usada e onde a
          apuração encontrou uma lacuna — CVM para fundamentos e B3 para preços.
        </p>
      </div>
    </div>
  );
}

function Fact({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="bg-canvas-900 px-4 py-4">
      <p className="text-xs text-copy-600">{label}</p>
      <p className="mt-2 truncate text-sm font-semibold text-copy-100" title={value}>
        {value}
      </p>
      <p className="mt-1 min-h-4 text-[0.65rem] text-copy-600">{detail || "—"}</p>
    </div>
  );
}

function BasesPanel({ analysis }: { analysis: Analysis }) {
  const rows = [
    ["Cotação", labelOf(analysis.price_basis)],
    ["Ações", labelOf(analysis.share_count_basis)],
    ["Liquidez", labelOf(analysis.liquidity_basis)],
    ["Dívida", labelOf(analysis.debt_basis)],
    ["Imposto do ROIC", labelOf(analysis.roic_tax_basis)],
    ["Referência", dateTime(analysis.computed_at)],
  ];

  return (
    <div className="rounded-lg border border-copy-200/10 bg-canvas-900 p-5">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-copy-200">Bases declaradas</h3>
          <p className="mt-1 text-xs leading-relaxed text-copy-600">
            Cada base limita o que pode ser comparado com segurança.
          </p>
        </div>
        <FiShield aria-hidden size={16} className="text-accent-400" />
      </header>

      <dl className="mt-4 grid gap-x-5 gap-y-3 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="border-t border-copy-200/10 pt-2.5">
            <dt className="text-[0.65rem] text-copy-600">{label}</dt>
            <dd className="mt-1 text-xs font-medium text-copy-300" title={value}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function CapitalPanel({
  analysis,
  marketValues,
  capital,
}: {
  analysis: Analysis;
  marketValues: ClassMarketValue[];
  capital: ShareCountProvenance | null;
}) {
  return (
    <div className="rounded-lg border border-copy-200/10 bg-canvas-900 p-5">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-copy-200">Capitalização por classe</h3>
          <p className="mt-1 text-xs leading-relaxed text-copy-600">
            O valor de mercado soma cada classe listada ao próprio preço e às ações em circulação.
          </p>
        </div>
        <FiLayers aria-hidden size={16} className="text-accent-400" />
      </header>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-4 border-t border-copy-200/10 pt-3">
        <div>
          <p className="text-[0.65rem] text-copy-600">Valor de mercado</p>
          <p className="nums mt-1 text-xl font-semibold text-copy-50">
            {money(analysis.indicators.market_cap)}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[0.65rem] text-copy-600">Ações em circulação</p>
          <p className="nums mt-1 text-sm font-semibold text-copy-300">
            {count(analysis.indicators.shares)}
          </p>
        </div>
      </div>

      {marketValues.length > 0 ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[34rem] text-left text-xs">
            <thead className="text-[0.62rem] uppercase tracking-wide text-copy-600">
              <tr>
                <th className="pb-2 font-medium">Classe</th>
                <th className="pb-2 text-right font-medium">Preço</th>
                <th className="pb-2 text-right font-medium">Ações</th>
                <th className="pb-2 text-right font-medium">Valor</th>
              </tr>
            </thead>
            <tbody>
              {marketValues.map((item) => (
                <tr key={item.class_id} className="border-t border-copy-200/10">
                  <td className="py-2.5">
                    <span className="nums font-semibold text-copy-200">{item.symbol}</span>
                    <span className="ml-2 text-[0.65rem] text-copy-600">{item.per_share_class}</span>
                  </td>
                  <td className="nums py-2.5 text-right text-copy-300">{price(item.price)}</td>
                  <td className="nums py-2.5 text-right text-copy-400">{count(item.shares)}</td>
                  <td className="nums py-2.5 text-right font-medium text-copy-200">{money(item.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-4 text-xs text-copy-600">Nenhuma composição por classe foi publicada para este período.</p>
      )}

      {capital && (
        <p className="mt-4 text-[0.65rem] text-copy-600">
          Fonte de capital: <span className="text-copy-400">{labelOf(capital.source)}</span> · ano arquivado {capital.filed_year ?? "—"} · fator de reexpressão {capital.restatement_factor ?? "—"}
        </p>
      )}
    </div>
  );
}

function EvidencePanel({ evidence }: { evidence: SourceAccountEvidence[] }) {
  return (
    <div className="rounded-lg border border-copy-200/10 bg-canvas-950 p-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-copy-200">Contas de origem</h3>
          <p className="mt-1 text-xs text-copy-600">O mapeamento que alimenta os indicadores.</p>
        </div>
        <FiGitBranch aria-hidden size={15} className="text-accent-400" />
      </header>

      {evidence.length === 0 ? (
        <p className="mt-4 text-xs text-copy-600">A API não publicou evidências para este período.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[32rem] text-left text-xs">
            <thead className="text-[0.62rem] uppercase tracking-wide text-copy-600">
              <tr>
                <th className="pb-2 font-medium">Campo</th>
                <th className="pb-2 font-medium">Demonstração</th>
                <th className="pb-2 text-right font-medium">Estado</th>
              </tr>
            </thead>
            <tbody>
              {evidence.map((item) => (
                <tr key={`${item.statement}-${item.field}`} className="border-t border-copy-200/10">
                  <td className="py-2 text-copy-300">
                    {labelOf(item.field)}
                    {item.field === "free_float" && item.expected.find((value) => value.startsWith("assembly_date=")) ? (
                      <span className="ml-2 text-copy-600">
                        posição de {item.expected.find((value) => value.startsWith("assembly_date="))?.slice("assembly_date=".length)}
                      </span>
                    ) : null}
                  </td>
                  <td className="py-2 text-copy-600">{item.statement}</td>
                  <td className={`py-2 text-right ${statusClass(item.status)}`}>{labelOf(item.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ContractPanel({ count }: { count: number }) {
  return (
    <div className="rounded-lg border border-copy-200/10 bg-canvas-950 p-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-copy-200">Contratos dos indicadores</h3>
          <p className="mt-1 text-xs leading-relaxed text-copy-600">
            A fórmula, o período, a base de preço e a base de ações de cada múltiplo.
          </p>
        </div>
        <FiInfo aria-hidden size={15} className="text-accent-400" />
      </header>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <SmallStat label="Fórmulas publicadas" value={String(count)} />
      </div>
      <p className="mt-4 text-xs leading-relaxed text-copy-600">
        Abra o ícone de informação de qualquer indicador para ver o contrato aplicado àquele número.
      </p>
    </div>
  );
}

function CapitalEvidence({ capital }: { capital: ShareCountProvenance | null }) {
  if (!capital) {
    return <EmptyEvidence title="Proveniência do capital" message="Sem composição de capital publicada." />;
  }
  const issued = capital.issued?.total ?? null;
  const outstanding = capital.outstanding?.total ?? null;
  const treasury = capital.treasury?.treasury_total ?? null;

  return (
    <div className="rounded-lg border border-copy-200/10 bg-canvas-950 p-4">
      <h3 className="text-sm font-semibold text-copy-200">Proveniência do capital</h3>
      <div className="mt-4 grid grid-cols-3 gap-2">
        <SmallStat label="Emitidas" value={count(issued)} />
        <SmallStat label="Circulação" value={count(outstanding)} />
        <SmallStat label="Tesouraria" value={count(treasury)} />
      </div>
      <p className="mt-4 text-xs text-copy-600">
        Estado: <span className={statusClass(capital.status)}>{labelOf(capital.status)}</span> · evidência: {capital.evidence.join(" · ") || "—"}
      </p>
      {capital.actions.length > 0 && <ActionList actions={capital.actions} />}
    </div>
  );
}

function DebtEvidencePanel({ evidence }: { evidence: DebtEvidence | null }) {
  if (!evidence) {
    return <EmptyEvidence title="Evidência da dívida" message="A API não publicou uma decisão de perímetro." />;
  }

  return (
    <div className="rounded-lg border border-copy-200/10 bg-canvas-950 p-4">
      <h3 className="text-sm font-semibold text-copy-200">Evidência da dívida</h3>
      <p className="mt-2 text-xs text-copy-600">
        Regime {labelOf(evidence.regime)} · identidade {labelOf(evidence.identity_status)}
      </p>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <SmallStat label="Linhas usadas" value={String(evidence.used_lines.length)} />
        <SmallStat label="Linhas excluídas" value={String(evidence.excluded_lines.length)} />
      </div>
      {(evidence.primary_blocker || evidence.secondary_blockers.length > 0) && (
        <p className="mt-4 text-xs text-warning">
          Limite: {[evidence.primary_blocker, ...evidence.secondary_blockers]
            .filter(Boolean)
            .map(labelOf)
            .join(" · ")}
        </p>
      )}
    </div>
  );
}

function ActionList({ actions }: { actions: CapitalAction[] }) {
  return (
    <div className="mt-4 border-t border-copy-200/10 pt-3">
      <p className="text-[0.65rem] uppercase tracking-wide text-copy-600">Eventos retidos</p>
      <ul className="mt-2 space-y-1.5 text-xs text-copy-400">
        {actions.map((action) => (
          <li key={`${action.approval_date}-${action.kind}`} className="flex items-center gap-2">
            <FiCheckCircle aria-hidden size={12} className="text-accent-400" />
            {action.approval_date} · {labelOf(action.kind)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function EmptyEvidence({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-lg border border-copy-200/10 bg-canvas-950 p-4">
      <h3 className="text-sm font-semibold text-copy-200">{title}</h3>
      <p className="mt-3 text-xs text-copy-600">{message}</p>
    </div>
  );
}

function SmallStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-copy-200/10 bg-canvas-900 px-2.5 py-2">
      <p className="text-[0.6rem] text-copy-600">{label}</p>
      <p className="nums mt-1 text-xs font-semibold text-copy-300">{value}</p>
    </div>
  );
}

function labelOf(value: string | null | undefined): string {
  if (!value) return "—";
  const labels: Record<string, string> = {
    absent: "ausente",
    available: "disponível",
    bank: "banco",
    b3_latest_close: "fechamento mais recente da B3",
    b3_year_end_close: "fechamento do ano na B3",
    br_statutory_34pct: "alíquota estatutária de 34%",
    closed_fiscal_year: "ano fechado",
    corporate: "corporativo",
    cvm_dfp: "DFP da CVM",
    cvm_fre: "FRE da CVM",
    cvm_latest_filed_outstanding_current_base: "último número em circulação arquivado pela CVM",
    cvm_year_end_outstanding_current_base: "número em circulação no fechamento do ano pela CVM",
    derived: "derivado",
    filed: "informado no documento",
    inapplicable_regime: "não aplicável ao regime",
    inventories: "Estoques",
    free_float: "Free Float",
    present_unreadable: "valor não legível",
    mapped: "mapeado",
    missing_cpc41_disclosure: "resultado por ação não divulgado",
    resolved: "resolvido",
    sector_fallback: "fallback do setor",
    selected: "selecionado",
    source_account_absent: "conta ausente",
    source_account_unmapped: "conta não mapeada",
    ttm_live: "período atual",
    unmapped: "não mapeado",
  };
  return labels[value] ?? value.replaceAll("_", " ");
}

function statusClass(status: string): string {
  if (["mapped", "derived", "resolved", "available", "selected"].includes(status)) {
    return "text-positive";
  }
  if (["absent", "unmapped", "missing", "inapplicable_regime"].includes(status)) {
    return "text-warning";
  }
  return "text-copy-400";
}
