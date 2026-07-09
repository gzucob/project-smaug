import Link from "next/link";
import { DragonMark } from "@/components/DragonMark";
import { TickerSearch } from "@/components/TickerSearch";

export function Navbar() {
  return (
    <header className="sticky top-0 z-50 border-b border-gold-500/10 bg-vault-950/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-5">
        <Link href="/" className="group flex items-center gap-3">
          <DragonMark size={34} className="transition-transform duration-500 group-hover:-rotate-6" />
          <span className="font-brand text-lg font-bold tracking-[0.28em] text-gold-molten">
            SMAUG
          </span>
        </Link>

        <nav className="ml-2 hidden items-center gap-1 text-sm text-ink-400 sm:flex">
          <NavLink href="/">Início</NavLink>
          <NavLink href="/portfolio">Carteira</NavLink>
        </nav>

        <div className="ml-auto w-40 sm:w-56">
          <TickerSearch compact />
        </div>
      </div>
    </header>
  );
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="rounded-lg px-3 py-1.5 font-medium transition-colors hover:bg-gold-500/10 hover:text-gold-300"
    >
      {children}
    </Link>
  );
}
