"use client";

import { useRouter } from "next/navigation";
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
        className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-500 transition-colors group-focus-within:text-gold-400"
      >
        <SearchGlyph />
      </span>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={compact ? "Ticker…" : "Buscar ticker (ex.: PETR4)"}
        aria-label="Buscar ticker"
        autoComplete="off"
        spellCheck={false}
        className={`nums w-full rounded-xl border border-gold-500/15 bg-vault-900/80 pl-9 pr-3 uppercase tracking-wider text-ink-50 placeholder:text-ink-600 placeholder:normal-case placeholder:tracking-normal outline-none transition-all focus:border-gold-400/60 focus:bg-vault-850 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--color-gold-500)_18%,transparent)] ${
          compact ? "h-9 text-sm" : "h-12 text-base"
        }`}
      />
    </form>
  );
}

function SearchGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
