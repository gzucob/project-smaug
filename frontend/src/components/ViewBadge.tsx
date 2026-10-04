/** Labels the analysis perspective: current rolling period vs a closed year. */
export function ViewBadge({ view, year }: { view: string; year?: string }) {
  const live = view === "ttm_live";
  return (
    <span className="inline-flex items-center gap-2 rounded-md border border-copy-200/10 bg-canvas-850 px-2.5 py-1 text-[0.7rem] font-medium tracking-wide text-copy-400">
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: live ? "var(--color-accent-400)" : "var(--color-copy-500)" }}
      />
      {live ? "Atual" : `Ano ${year ?? ""}`.trim()}
    </span>
  );
}
