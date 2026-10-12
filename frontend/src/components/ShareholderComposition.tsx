import { count, dateOnly, toNum } from "@/lib/format";
import type { ShareholderPosition, ShareholdingPosition } from "@/lib/types";

const FILED_PERCENT_FORMAT = new Intl.NumberFormat("pt-BR", {
  maximumFractionDigits: 4,
});

function filedPercent(value: ShareholderPosition["total_percent"]): string {
  const number = toNum(value);
  return number === null ? "—" : `${FILED_PERCENT_FORMAT.format(number)}%`;
}

function holderType(value: string | null): string {
  if (value === "PF") return "Pessoa física";
  if (value === "PJ") return "Pessoa jurídica";
  return value ?? "—";
}

function missingMessage(position: ShareholdingPosition | null): string {
  if (position === null) {
    return "Não foi possível consultar a posição acionária na API.";
  }
  if (position.coverage === "unavailable") {
    return "Ainda não há uma posição acionária válida espelhada para este ticker.";
  }
  return "O FRE consultado não trouxe posições diretas para exibição.";
}

export function ShareholderComposition({
  position,
}: {
  position: ShareholdingPosition | null;
}) {
  const availablePosition = position?.coverage === "available" ? position : null;
  const rows = availablePosition?.positions ?? [];

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight text-copy-100">
            Composição acionária declarada
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-copy-500">
            Posições diretas identificadas no Formulário de Referência da companhia.
          </p>
        </div>
        {availablePosition?.reference_date ? (
          <p className="text-xs text-copy-500">
            Referência do FRE:{" "}
            <time dateTime={availablePosition.reference_date}>
              {dateOnly(availablePosition.reference_date)}
            </time>
            {availablePosition.version !== null
              ? ` · versão ${availablePosition.version}`
              : ""}
            {availablePosition.archive_year !== null
              ? ` · arquivo ${availablePosition.archive_year}`
              : ""}
          </p>
        ) : null}
      </header>

      {availablePosition && rows.length > 0 ? (
        <div className="panel min-w-0 overflow-hidden">
          <div className="max-w-full overflow-x-auto">
            <table className="w-full min-w-[880px] border-collapse text-left text-xs">
              <caption className="sr-only">
                Acionistas diretos e percentuais informados no FRE de{" "}
                {availablePosition.ticker}
              </caption>
              <thead className="border-b border-copy-200/10 bg-canvas-900/60 text-[0.65rem] uppercase tracking-wide text-copy-500">
                <tr>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Acionista
                  </th>
                  <th scope="col" className="px-4 py-3 text-right font-semibold">
                    Ações ON
                  </th>
                  <th scope="col" className="px-4 py-3 text-right font-semibold">
                    % ON
                  </th>
                  <th scope="col" className="px-4 py-3 text-right font-semibold">
                    Ações PN
                  </th>
                  <th scope="col" className="px-4 py-3 text-right font-semibold">
                    % PN
                  </th>
                  <th scope="col" className="px-4 py-3 text-right font-semibold">
                    Total de ações
                  </th>
                  <th scope="col" className="px-4 py-3 text-right font-semibold">
                    % total informado
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-copy-200/10">
                {rows.map((holder, index) => (
                  <tr key={`${holder.name}-${index}`} className="text-copy-200">
                    <th
                      scope="row"
                      className="max-w-[24rem] px-4 py-3 font-medium text-copy-100"
                    >
                      <span className="block truncate" title={holder.name}>
                        {holder.name}
                      </span>
                      <span className="mt-1 block text-[0.65rem] font-normal text-copy-500">
                        {holderType(holder.shareholder_type)}
                        {holder.is_controller === true ? " · controlador" : ""}
                      </span>
                      {holder.class_positions.length > 0 ? (
                        <span className="mt-1 block max-w-[24rem] text-[0.65rem] font-normal text-copy-600">
                          Por classe: {holder.class_positions.map((item) =>
                            `${item.share_class} ${count(item.shares)} (${filedPercent(item.percent)})`,
                          ).join(" · ")}
                        </span>
                      ) : null}
                    </th>
                    <td className="nums whitespace-nowrap px-4 py-3 text-right">
                      {count(holder.ordinary_shares)}
                    </td>
                    <td className="nums whitespace-nowrap px-4 py-3 text-right">
                      {filedPercent(holder.ordinary_percent)}
                    </td>
                    <td className="nums whitespace-nowrap px-4 py-3 text-right">
                      {count(holder.preferred_shares)}
                    </td>
                    <td className="nums whitespace-nowrap px-4 py-3 text-right">
                      {filedPercent(holder.preferred_percent)}
                    </td>
                    <td className="nums whitespace-nowrap px-4 py-3 text-right">
                      {count(holder.total_shares)}
                    </td>
                    <td className="nums whitespace-nowrap px-4 py-3 text-right font-semibold text-copy-100">
                      {filedPercent(holder.total_percent)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="border-t border-copy-200/10 px-4 py-4 text-xs leading-relaxed text-copy-500">
            <p>
              Percentuais exibidos como declarados no FRE. As posições ligadas a
              outros titulares foram preservadas na ingestão e não são somadas
              aqui, pois podem descrever a mesma cadeia de controle.
            </p>
            <p className="mt-2">
              O FRE identifica controladores e outros acionistas que atendem aos
              critérios de divulgação, incluindo participações de 5% ou mais por
              classe ou espécie. A diferença não permite deduzir uma participação
              agregada de acionistas minoritários.
            </p>
            <a
              href="https://dados.cvm.gov.br/dataset/cia_aberta-doc-fre"
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex text-accent-300 underline decoration-accent-400/50 underline-offset-4 hover:text-accent-200"
            >
              Dados estruturados do FRE na CVM
            </a>
            <span aria-hidden="true" className="mx-2 text-copy-700">·</span>
            <a
              href="https://conteudo.cvm.gov.br/export/sites/cvm/legislacao/oficios-circulares/sep/anexos/oc-anual-sep-2026.pdf"
              target="_blank"
              rel="noreferrer"
              className="inline-flex text-accent-300 underline decoration-accent-400/50 underline-offset-4 hover:text-accent-200"
            >
              Orientação da CVM sobre os quadros do FRE
            </a>
          </div>
        </div>
      ) : (
        <p role="status" className="panel p-4 text-sm leading-relaxed text-copy-500">
          {missingMessage(position)}
        </p>
      )}
    </div>
  );
}
