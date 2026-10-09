"use client";

import { useState, useTransition } from "react";
import { FiHeart, FiLoader } from "react-icons/fi";
import { addFavorite, removeFavorite } from "@/lib/api";

/**
 * Heart toggle for the portfolio (#151). Initial state is server-rendered
 * (`favorited` prop) — no client fetch on mount, matching every other
 * component here. Waits for the real response before flipping the icon
 * rather than updating optimistically. Pending and failed writes are announced
 * without changing the confirmed membership.
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
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");

  function toggle() {
    setError(null);
    setStatus("Salvando favoritos…");
    startTransition(async () => {
      const result = favorited ? await removeFavorite(ticker) : await addFavorite(ticker);
      if (result.ok) {
        setFavorited(!favorited);
        setStatus(favorited ? `${ticker} removido dos favoritos.` : `${ticker} adicionado aos favoritos.`);
      } else {
        setStatus("");
        setError("Não foi possível atualizar os favoritos. Tente novamente.");
      }
    });
  }

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={toggle}
        disabled={pending}
        aria-busy={pending}
        aria-pressed={favorited}
        aria-label={favorited ? `Remover ${ticker} dos favoritos` : `Adicionar ${ticker} aos favoritos`}
        className={`pressable flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border transition-colors duration-200 ease-[var(--ease-out-strong)] disabled:opacity-60 ${
          favorited
            ? "border-accent-400/50 text-down hover:border-accent-300/70 hover:text-down"
            : "border-copy-200/15 text-copy-400 hover:border-accent-400/50 hover:text-accent-300"
        }`}
      >
        {pending ? (
          <span className="animate-spin motion-reduce:animate-none" aria-hidden>
            <FiLoader size={18} />
          </span>
        ) : (
          <FiHeart size={18} fill={favorited ? "currentColor" : "none"} aria-hidden />
        )}
      </button>
      <span role="status" className="sr-only">{status}</span>
      {error && (
        <p role="alert" className="absolute left-1/2 top-full z-10 mt-2 w-48 -translate-x-1/2 rounded-md border border-copy-200/20 bg-canvas-900 p-3 text-xs leading-relaxed text-copy-200">
          {error}
        </p>
      )}
    </div>
  );
}
