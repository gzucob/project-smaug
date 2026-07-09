import Link from "next/link";
import { DragonMark } from "@/components/DragonMark";

export default function NotFound() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-6 px-5 py-28 text-center">
      <DragonMark size={80} withFlame />
      <h1 className="font-brand text-3xl tracking-[0.2em] text-gold-molten">404</h1>
      <p className="text-ink-400">
        O dragão não encontrou esta página no tesouro.
      </p>
      <Link
        href="/"
        className="rounded-xl border border-gold-500/30 px-5 py-2.5 text-sm font-semibold text-gold-300 transition-colors hover:bg-gold-500/10"
      >
        Voltar ao início
      </Link>
    </div>
  );
}
