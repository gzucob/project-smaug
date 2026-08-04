"use client";

import { useState, useTransition } from "react";
import { FiHeart } from "react-icons/fi";
import { addFavorite, removeFavorite } from "@/lib/api";

/**
 * Heart toggle for the portfolio (#151). Initial state is server-rendered
 * (`favorited` prop) — no client fetch on mount, matching every other
 * component here. Waits for the real response before flipping the icon
 * rather than updating optimistically: this app talks to a self-hosted
 * backend, so the round trip is imperceptible, and skipping optimistic
 * rollback keeps the component to one state variable.
 */
export function FavoriteButton({
  ticker,
  favorited: initial,
}: {
  ticker: string;
  favorited: boolean;
}) {
  const [favorited, setFavorited] = useState(initial);
  const [pending, startTransition] = useTransition();

  function toggle() {
    startTransition(async () => {
      const result = favorited ? await removeFavorite(ticker) : await addFavorite(ticker);
      if (result.ok) setFavorited(!favorited);
    });
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={pending}
      aria-pressed={favorited}
      aria-label={favorited ? `Remover ${ticker} da carteira` : `Adicionar ${ticker} à carteira`}
      className={`pressable flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border transition-colors duration-200 ease-[var(--ease-out-strong)] disabled:opacity-60 ${
        favorited
          ? "border-gold-400/40 text-gold-400 hover:border-gold-400/60 hover:text-gold-300"
          : "border-gold-500/15 text-ink-400 hover:border-gold-400/50 hover:text-gold-300"
      }`}
    >
      <FiHeart size={18} fill={favorited ? "currentColor" : "none"} aria-hidden />
    </button>
  );
}
