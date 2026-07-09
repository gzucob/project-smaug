import Link from "next/link";
import { DragonMark } from "@/components/DragonMark";
import { API_BASE } from "@/lib/api";

/** Friendly empty/error state when the backend can't be reached (or 404). */
export function VaultOffline({
  message,
  title = "O cofre está fechado",
  showBackHome = false,
}: {
  message: string;
  title?: string;
  showBackHome?: boolean;
}) {
  return (
    <div className="mx-auto flex max-w-xl flex-col items-center gap-5 px-5 py-24 text-center">
      <DragonMark size={72} withFlame className="opacity-90" />
      <h2 className="font-display text-2xl text-ink-100">{title}</h2>
      <p className="text-sm leading-relaxed text-ink-400">{message}</p>
      <div className="panel w-full p-4 text-left">
        <p className="mb-2 text-xs uppercase tracking-wide text-ink-500">Para acordar o dragão</p>
        <pre className="nums overflow-x-auto rounded-lg bg-vault-950 p-3 text-xs text-gold-300">
          uvicorn smaug.entrypoints.api:app --reload
        </pre>
        <p className="mt-2 text-[0.68rem] text-ink-600">
          API esperada em <span className="text-ink-400">{API_BASE}</span> — ajuste com{" "}
          <span className="text-ink-400">NEXT_PUBLIC_API_BASE</span>.
        </p>
      </div>
      {showBackHome && (
        <Link
          href="/"
          className="rounded-xl border border-gold-500/30 px-5 py-2.5 text-sm font-semibold text-gold-300 transition-colors hover:bg-gold-500/10"
        >
          Voltar ao início
        </Link>
      )}
    </div>
  );
}
