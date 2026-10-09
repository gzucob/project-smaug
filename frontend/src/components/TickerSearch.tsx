"use client";

import { useRouter } from "next/navigation";
import { FiSearch } from "react-icons/fi";
import { useState } from "react";

/** Ticker lookup — navigates to the detail route; no client-side fetch. */
export function TickerSearch({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const [value, setValue] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const symbol = value.trim().toUpperCase();
    if (symbol) router.push(`/ticker/${encodeURIComponent(symbol)}`);
  }

  return (
    <form onSubmit={submit} className="group relative">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={compact ? "Ticker…" : "Buscar ticker (ex.: PETR4)"}
        aria-label="Buscar ticker"
        autoComplete="off"
        enterKeyHint="search"
        spellCheck={false}
        className={`nums w-full rounded-md border border-copy-200/10 bg-canvas-900 pl-3 pr-12 uppercase tracking-wider text-copy-50 placeholder:text-copy-600 placeholder:normal-case placeholder:tracking-normal outline-none transition-[border-color,background-color,box-shadow] duration-200 ease-[var(--ease-out-strong)] focus:border-accent-400/60 focus:bg-canvas-850 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--color-accent-500)_18%,transparent)] ${
          compact ? "h-11 text-base sm:text-sm" : "h-12 text-base"
        }`}
      />
      <button
        type="submit"
        aria-label="Buscar ticker"
        disabled={!value.trim()}
        className="pressable absolute right-0.5 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-md text-copy-400 hover:bg-copy-200/8 hover:text-accent-300 disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent-400"
      >
        <FiSearch size={16} aria-hidden />
      </button>
    </form>
  );
}
