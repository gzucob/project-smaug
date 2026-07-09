/**
 * Sector → gemstone identity. Each of the five portfolio sectors owns one vivid
 * hue (a CSS variable from globals.css) used consistently across the UI as a
 * data-encoding system. Labels are PT-BR (user-facing text convention).
 */
import type { SectorKey } from "@/lib/types";

export interface SectorMeta {
  key: SectorKey;
  label: string;
  gem: string; // gemstone name for the accent
  colorVar: string; // CSS custom property holding the hue
}

export const SECTORS: Record<SectorKey, SectorMeta> = {
  bank: { key: "bank", label: "Bancos", gem: "Safira", colorVar: "--color-gem-azure" },
  insurer: { key: "insurer", label: "Seguradoras", gem: "Ametista", colorVar: "--color-gem-violet" },
  utility: { key: "utility", label: "Utilities", gem: "Esmeralda", colorVar: "--color-gem-jade" },
  commodity: { key: "commodity", label: "Commodities", gem: "Ouro", colorVar: "--color-gem-gold" },
  industry: { key: "industry", label: "Indústria", gem: "Rubi", colorVar: "--color-gem-coral" },
};

const FALLBACK: SectorMeta = {
  key: "industry",
  label: "Setor",
  gem: "—",
  colorVar: "--color-ink-400",
};

export function sectorMeta(key: string): SectorMeta {
  return SECTORS[key as SectorKey] ?? FALLBACK;
}

export function sectorColor(key: string): string {
  return `var(${sectorMeta(key).colorVar})`;
}

/** The target portfolio in stable order (mirrors PORTFOLIO in sectors.py). */
export const PORTFOLIO: { ticker: string; sector: SectorKey }[] = [
  { ticker: "PETR4", sector: "commodity" },
  { ticker: "VALE3", sector: "commodity" },
  { ticker: "SAPR11", sector: "utility" },
  { ticker: "TAEE11", sector: "utility" },
  { ticker: "WEGE3", sector: "industry" },
  { ticker: "BBAS3", sector: "bank" },
  { ticker: "BBDC4", sector: "bank" },
  { ticker: "BBSE3", sector: "insurer" },
  { ticker: "CXSE3", sector: "insurer" },
];
