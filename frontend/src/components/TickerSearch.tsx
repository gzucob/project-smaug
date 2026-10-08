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
      <span
        aria-hidden
        className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-copy-500 transition-colors group-focus-within:text-accent-400"
      >
        <FiSearch size={16} />
      </span>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={compact ? "Ticker…" : "Buscar ticker (ex.: PETR4)"}
        aria-label="Buscar ticker"
        autoComplete="off"
        spellCheck={false}
        className={`nums w-full rounded-md border border-copy-200/10 bg-canvas-900 pl-9 pr-3 uppercase tracking-wider text-copy-50 placeholder:text-copy-600 placeholder:normal-case placeholder:tracking-normal outline-none transition-[border-color,background-color,box-shadow] duration-200 ease-[var(--ease-out-strong)] focus:border-accent-400/60 focus:bg-canvas-850 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--color-accent-500)_18%,transparent)] ${
          compact ? "h-9 text-sm" : "h-12 text-base"
        }`}
      />
    </form>
  );
}
