import Link from "next/link";
import { DragonMark } from "@/components/DragonMark";
import { TickerSearch } from "@/components/TickerSearch";

export function Navbar() {
  return (
    <header className="sticky top-0 z-50 border-b border-copy-200/10 bg-canvas-950/95">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-5 px-5">
        <Link href="/" className="group flex items-center gap-2.5 text-copy-50">
          <DragonMark size={27} />
          <span className="font-brand text-sm font-semibold tracking-[0.22em]">SMAUG</span>
        </Link>

        <nav className="ml-2 hidden items-center gap-1 text-sm text-copy-500 sm:flex">
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
      className="rounded-md px-3 py-1.5 font-medium transition-colors hover:bg-copy-200/8 hover:text-copy-50"
    >
      {children}
    </Link>
  );
}
