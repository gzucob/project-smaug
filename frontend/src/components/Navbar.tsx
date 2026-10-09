import Link from "next/link";
import { FiHeart } from "react-icons/fi";
import { DragonMark } from "@/components/DragonMark";
import { TickerSearch } from "@/components/TickerSearch";

export function Navbar() {
  return (
    <header className="sticky top-0 z-50 border-b border-copy-200/10 bg-canvas-950/95">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-2 px-4 sm:gap-5 sm:px-5">
        <Link href="/" aria-label="Smaug — início" className="group flex shrink-0 items-center gap-2.5 text-copy-50">
          <DragonMark size={27} />
          <span className="hidden font-brand text-sm font-semibold tracking-[0.22em] min-[400px]:inline">SMAUG</span>
        </Link>

        <nav className="ml-2 hidden items-center gap-1 text-sm text-copy-500 sm:flex">
          <NavLink href="/">Início</NavLink>
          <NavLink href="/portfolio">Favoritos</NavLink>
        </nav>

        <Link
          href="/portfolio"
          aria-label="Favoritos"
          className="pressable ml-auto flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-copy-300 hover:bg-copy-200/8 hover:text-copy-50 focus-visible:outline-2 focus-visible:outline-accent-400 sm:hidden"
        >
          <FiHeart size={18} aria-hidden />
        </Link>

        <div className="w-36 min-w-0 shrink sm:ml-auto sm:w-56">
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
      className="rounded-md px-3 py-1.5 font-medium transition-colors hover:bg-copy-200/8 hover:text-copy-50"
    >
      {children}
    </Link>
  );
}
