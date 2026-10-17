"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Listbox,
  ListboxButton,
  ListboxOption,
  ListboxOptions,
} from "@headlessui/react";
import { FiChevronDown, FiSearch } from "react-icons/fi";
import { multiple, pct, price, toNum } from "@/lib/format";
import type { AnalysisSummary, Decimalish } from "@/lib/types";

const PAGE_SIZE = 10;
const ALL_SECTORS = "";
const fold = (value: string) =>
  value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
const available = (
  value: Decimalish | undefined,
  format: (value: Decimalish) => string,
) => (toNum(value) === null ? "n/d" : format(value ?? null));

/** Search and filter persisted summaries; all reads stay on the server. */
export function CompanyExplorer({ analyses }: { analyses: AnalysisSummary[] }) {
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState(ALL_SECTORS);
  const [page, setPage] = useState(0);
  const sectors = [
    ...new Set(analyses.map((item) => item.classification.setor)),
  ].sort((a, b) => a.localeCompare(b, "pt-BR"));
  const needle = fold(query.trim());
  const filtered = analyses
    .filter(
      (item) =>
        (!sector || item.classification.setor === sector) &&
        fold(`${item.ticker} ${item.issuer ?? ""}`).includes(needle),
    )
    .sort((a, b) => a.ticker.localeCompare(b.ticker));
  const lastPage = Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1);
  const currentPage = Math.min(page, lastPage);
  const start = currentPage * PAGE_SIZE;
  const visible = filtered.slice(start, start + PAGE_SIZE);

  return (
    <section
      id="explore-companies"
      aria-labelledby="explore-title"
      className="min-w-0 scroll-mt-24"
    >
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2
            id="explore-title"
            className="text-xl font-semibold text-copy-100"
          >
            Explorar empresas
          </h2>
        </div>
        <p aria-live="polite" className="nums text-xs text-copy-500">
          {filtered.length}{" "}
          {filtered.length === 1 ? "ativo disponível" : "ativos disponíveis"}
        </p>
      </div>
      <div className="panel overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-copy-200/10 p-4 sm:flex-row">
          <label className="flex min-w-0 flex-1 items-center gap-3 rounded-lg border border-copy-200/15 bg-canvas-950 px-3 focus-within:border-accent-400">
            <FiSearch aria-hidden className="shrink-0 text-copy-500" />
            <span className="sr-only">Filtrar por ticker ou empresa</span>
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
              placeholder="Ticker ou nome da empresa"
              className="min-w-0 flex-1 bg-transparent py-2.5 text-sm text-copy-100 outline-none placeholder:text-copy-600"
            />
          </label>
          <Listbox
            value={sector}
            onChange={(value) => {
              setSector(value);
              setPage(0);
            }}
          >
            <ListboxButton
              aria-label="Filtrar por setor"
              className="flex items-center justify-between gap-3 rounded-lg border border-copy-200/15 px-3 py-2.5 text-left text-sm text-copy-300 focus-visible:outline-2 focus-visible:outline-accent-400 sm:w-64"
            >
              <span className="truncate">{sector || "Todos os setores"}</span>
              <FiChevronDown aria-hidden className="shrink-0" />
            </ListboxButton>
            <ListboxOptions
              anchor="bottom end"
              className="panel z-50 w-[min(20rem,calc(100vw-2.5rem))] overflow-y-auto p-1 focus:outline-none [--anchor-gap:0.5rem] [--anchor-max-height:18rem]"
            >
              {[ALL_SECTORS, ...sectors].map((value) => (
                <ListboxOption
                  key={value}
                  value={value}
                  className="cursor-pointer rounded-md px-3 py-2 text-sm text-copy-300 data-focus:bg-canvas-800 data-selected:text-accent-300"
                >
                  {value || "Todos os setores"}
                </ListboxOption>
              ))}
            </ListboxOptions>
          </Listbox>
        </div>
        {visible.length === 0 ? (
          <div className="px-5 py-10 text-center">
            <p className="text-sm text-copy-300">
              {analyses.length === 0
                ? "Nenhuma análise disponível ainda."
                : "Nenhum ativo encontrado com esses filtros."}
            </p>
            {(query || sector) && (
              <button
                onClick={() => {
                  setQuery("");
                  setSector(ALL_SECTORS);
                  setPage(0);
                }}
                className="pressable mt-3 text-sm text-accent-300"
              >
                Limpar filtros
              </button>
            )}
          </div>
        ) : (
          <div>
            <p className="border-b border-copy-200/10 px-5 py-2 text-xs text-copy-500 sm:hidden">
              Deslize a tabela para ver os indicadores →
            </p>
            <div
              role="region"
              aria-label="Tabela de ativos e indicadores"
              tabIndex={0}
              className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-accent-400"
            >
              <table className="w-full min-w-[640px] text-left text-sm">
                <caption className="sr-only">
                  Ativos disponíveis, cotação e indicadores por período
                </caption>
                <thead className="bg-canvas-950/40 text-xs text-copy-500">
                  <tr>
                    <th scope="col" className="px-5 py-3 font-medium">
                      Empresa / ativo
                    </th>
                    <th scope="col" className="px-3 py-3 font-medium">
                      Período
                    </th>
                    {["Cotação", "P/L", "ROE", "Dividend yield"].map(
                      (label) => (
                        <th
                          key={label}
                          scope="col"
                          className="px-4 py-3 text-right font-medium"
                        >
                          {label}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-copy-200/10">
                  {visible.map((item) => (
                    <tr key={item.ticker} className="hover:bg-canvas-800/40">
                      <th scope="row" className="px-5 py-3 font-normal">
                        <Link
                          href={`/ticker/${item.ticker}`}
                          className="pressable block rounded-sm focus-visible:outline-2 focus-visible:outline-accent-400"
                        >
                          <span className="nums font-semibold text-copy-100">
                            {item.ticker}{" "}
                            <span aria-hidden className="text-copy-600">
                              ↗
                            </span>
                          </span>
                          <span className="mt-0.5 block max-w-64 truncate text-xs text-copy-400">
                            {item.issuer ?? item.classification.setor}
                          </span>
                        </Link>
                      </th>
                      <td className="whitespace-nowrap px-3 py-3 text-xs text-copy-500">
                        {item.view === "ttm_live"
                          ? "12 meses"
                          : item.reference_date.slice(0, 4)}
                      </td>
                      <td className="nums whitespace-nowrap px-4 py-3 text-right text-copy-200">
                        {available(item.price_as_traded ?? item.price, price)}
                      </td>
                      <td className="nums px-4 py-3 text-right text-copy-200">
                        {available(item.indicators.pe_basic, multiple)}
                      </td>
                      <td className="nums px-4 py-3 text-right text-copy-200">
                        {available(item.indicators.roe, pct)}
                      </td>
                      <td className="nums px-4 py-3 text-right text-copy-200">
                        {available(item.indicators.dividend_yield, pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {filtered.length > PAGE_SIZE && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-copy-200/10 px-5 py-3">
            <p className="nums text-xs text-copy-500">
              {start + 1}–{Math.min(start + PAGE_SIZE, filtered.length)} de{" "}
              {filtered.length}
            </p>
            <div className="flex gap-4">
              <button
                disabled={currentPage === 0}
                onClick={() => setPage(currentPage - 1)}
                className="pressable text-xs text-copy-300 disabled:opacity-35"
              >
                ← Anterior
              </button>
              <button
                disabled={currentPage === lastPage}
                onClick={() => setPage(currentPage + 1)}
                className="pressable text-xs text-copy-300 disabled:opacity-35"
              >
                Próxima →
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
