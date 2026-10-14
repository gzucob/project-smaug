"use client";

import { Combobox, ComboboxInput, ComboboxOption, ComboboxOptions } from "@headlessui/react";
import { useRouter } from "next/navigation";
import { FiSearch } from "react-icons/fi";
import { useMemo, useState } from "react";

interface SearchOption {
  ticker: string;
  issuer?: string | null;
}

function normalize(value: string) {
  return value.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("pt-BR").trim();
}

/** Filter server-provided names and symbols without browser data requests. */
export function TickerSearchInput({
  compact = false,
  options,
  loading = false,
  unavailable = false,
}: {
  compact?: boolean;
  options: SearchOption[];
  loading?: boolean;
  unavailable?: boolean;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const indexed = useMemo(() => options.map((option) => ({
    ...option,
    symbol: normalize(option.ticker),
    name: normalize(option.issuer ?? ""),
  })), [options]);
  const term = normalize(query);
  const matches = term
    ? indexed.filter((option) => option.symbol.includes(term) || option.name.includes(term))
      .sort((a, b) => {
        const rank = (symbol: string) => symbol === term ? 0 : symbol.startsWith(term) ? 1 : 2;
        return rank(a.symbol) - rank(b.symbol) || a.ticker.localeCompare(b.ticker);
      })
    : [];

  function navigate(option: SearchOption | null) {
    if (option) {
      router.push(`/ticker/${encodeURIComponent(option.ticker)}`);
      setQuery("");
    }
  }

  const directSymbol = /^[a-z]{4}\d{1,2}$/i.test(query.trim());
  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (matches.length) navigate(matches[0]);
    else if (directSymbol) {
      router.push(`/ticker/${encodeURIComponent(query.trim().toUpperCase())}`);
      setQuery("");
    }
  }

  return (
    <form onSubmit={submit} className="group relative">
      <Combobox<SearchOption | null> value={null} onChange={navigate}>
        <ComboboxInput
          value={query}
          disabled={loading}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={compact ? "Ticker ou nome…" : "Buscar ticker ou empresa"}
          aria-label="Buscar por ticker ou nome da empresa"
          autoComplete="off"
          enterKeyHint="search"
          spellCheck={false}
          className={`w-full rounded-md border border-copy-200/10 bg-canvas-900 pl-3 pr-12 text-copy-50 placeholder:text-copy-600 outline-none transition-[border-color,background-color,box-shadow] duration-200 ease-[var(--ease-out-strong)] focus:border-accent-400/60 focus:bg-canvas-850 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--color-accent-500)_18%,transparent)] ${
            compact ? "h-11 text-base sm:text-sm" : "h-12 text-base"
          }`}
        />
        <ComboboxOptions
          anchor="bottom end"
          className="z-[60] w-[max(var(--input-width),16rem)] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-md border border-copy-200/15 bg-canvas-900 p-1 text-left [--anchor-gap:0.5rem] [--anchor-max-height:20rem] empty:invisible"
        >
          {term && matches.map((option) => (
            <ComboboxOption
              key={option.ticker}
              value={option}
              className="cursor-pointer rounded px-3 py-2.5 text-copy-300 data-focus:bg-copy-200/8 data-focus:text-copy-50"
            >
              <span className="nums block text-sm font-semibold tracking-wide">{option.ticker}</span>
              {option.issuer && <span className="block text-xs text-copy-500">{option.issuer}</span>}
            </ComboboxOption>
          ))}
          {term && matches.length === 0 && (
            <p role="status" className="px-3 py-3 text-sm text-copy-500">
              {loading ? "Carregando opções…" : unavailable ? "Busca por nome indisponível agora." : "Nenhum ticker ou empresa encontrado."}
            </p>
          )}
        </ComboboxOptions>
      </Combobox>
      <button
        type="submit"
        aria-label="Buscar ticker ou empresa"
        disabled={!matches.length && !directSymbol}
        className="pressable absolute right-0.5 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-md text-copy-400 hover:bg-copy-200/8 hover:text-accent-300 disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent-400"
      >
        <FiSearch size={16} aria-hidden />
      </button>
    </form>
  );
}
