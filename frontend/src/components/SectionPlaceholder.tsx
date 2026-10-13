/** Reserve space while a server section streams, without decorative motion. */
export function SectionPlaceholder({
  label,
  tall = false,
}: {
  label: string;
  tall?: boolean;
}) {
  return (
    <div
      aria-busy="true"
      aria-label={label}
      className={`panel p-5 ${tall ? "min-h-64" : "min-h-20"}`}
    >
      <span className="sr-only">
        Carregando {label.toLocaleLowerCase("pt-BR")}
      </span>
      <div aria-hidden className="h-3 w-32 rounded bg-copy-200/10" />
    </div>
  );
}
