import type { Metadata } from "next";
import { Geist, Geist_Mono, Newsreader } from "next/font/google";
import "./globals.css";
import { Navbar } from "@/components/Navbar";
import { SiteFooter } from "@/components/SiteFooter";

// Modern, corporate type system (Anthropic-adjacent): a clean grotesque sans
// for UI + wordmark, a sober editorial serif for display headings, and a
// monospace for tabular data.
const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
  display: "swap",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

const newsreader = Newsreader({
  subsets: ["latin"],
  style: ["normal", "italic"],
  variable: "--font-newsreader",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Smaug — análise da carteira",
  description:
    "O dragão que guarda a sua carteira. Indicadores fundamentalistas em duas visões: TTM ao vivo e histórico de anos fechados.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="pt-BR"
      className={`${geist.variable} ${geistMono.variable} ${newsreader.variable}`}
    >
      <body className="font-body antialiased">
        <div className="relative flex min-h-dvh flex-col">
          <Navbar />
          <main className="flex-1">{children}</main>
          <SiteFooter />
        </div>
      </body>
    </html>
  );
}
