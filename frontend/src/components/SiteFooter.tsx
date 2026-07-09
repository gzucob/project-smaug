import { DragonMark } from "@/components/DragonMark";

export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-gold-500/10">
      <div className="mx-auto flex max-w-6xl flex-col items-center gap-3 px-5 py-10 text-center sm:flex-row sm:justify-between sm:text-left">
        <div className="flex items-center gap-2.5 text-ink-500">
          <DragonMark size={22} />
          <span className="font-brand text-sm tracking-[0.25em]">SMAUG</span>
        </div>
        <p className="max-w-md text-xs leading-relaxed text-ink-600">
          Ferramenta pessoal de análise da carteira. Indicadores derivados dos
          dados fundamentalistas — não é recomendação de investimento.
        </p>
      </div>
    </footer>
  );
}
