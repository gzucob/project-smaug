/** Labels the analysis perspective: trailing-twelve-months vs a closed year. */
export function ViewBadge({ view, year }: { view: string; year?: string }) {
  const live = view === "ttm_live";
  return (
    <span className="inline-flex items-center gap-2 rounded-md border border-white/8 bg-vault-850 px-2.5 py-1 text-[0.7rem] font-medium tracking-wide text-ink-400">
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: live ? "var(--color-gold-500)" : "var(--color-ink-500)" }}
      />
      {live ? "Últimos 12 meses" : `Exercício ${year ?? ""}`.trim()}
    </span>
  );
}
