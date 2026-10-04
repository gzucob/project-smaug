import Link from "next/link";
import { DragonMark } from "@/components/DragonMark";
import { API_BASE } from "@/lib/api";

/** Clear empty/error state when the backend can't be reached (or 404). */
export function VaultOffline({
  message,
  title = "Não foi possível carregar os dados",
  showBackHome = false,
}: {
  message: string;
  title?: string;
  showBackHome?: boolean;
}) {
  return (
    <div className="mx-auto flex max-w-xl flex-col items-center gap-5 px-5 py-24 text-center">
      <DragonMark size={72} withFlame className="opacity-90" />
      <h2 className="text-2xl font-semibold tracking-tight text-copy-100">{title}</h2>
      <p className="text-sm leading-relaxed text-copy-400">{message}</p>
      <div className="panel w-full p-4 text-left">
        <p className="mb-2 text-xs uppercase tracking-wide text-copy-500">Como iniciar a API</p>
        <pre className="nums overflow-x-auto rounded-lg bg-canvas-950 p-3 text-xs text-accent-300">
          uvicorn smaug.entrypoints.api:app --reload
        </pre>
        <p className="mt-2 text-[0.68rem] text-copy-600">
          API esperada em <span className="text-copy-400">{API_BASE}</span> — ajuste com{" "}
          <span className="text-copy-400">NEXT_PUBLIC_API_BASE</span>.
        </p>
      </div>
      {showBackHome && (
        <Link
          href="/"
          className="pressable rounded-lg border border-accent-500/30 px-5 py-2.5 text-sm font-semibold text-accent-300 hover:bg-accent-500/10"
        >
          Voltar ao início
        </Link>
      )}
    </div>
  );
}
