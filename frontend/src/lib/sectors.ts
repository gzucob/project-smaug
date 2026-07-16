/**
 * Sector → gemstone identity. Each of the five portfolio sectors owns one vivid
 * hue (a CSS variable from globals.css) used consistently across the UI as a
 * data-encoding system. Labels are PT-BR (user-facing text convention).
 */
import type { Classification, SectorKey } from "@/lib/types";

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

/**
 * Map a B3 classification (or the CVM single-level fallback) to one of the five
 * gemstone hues. The gem system stays the visual encoding; the real three-level
 * taxonomy is what the UI now *shows* (ADR 0024). Matches by folded substring so
 * it works for both B3 labels ("Financeiro" + "Bancos") and CVM ones ("Bancos",
 * "Papel e Celulose").
 */
export function gemKey(c: Classification): SectorKey {
  const t = `${c.setor} ${c.subsetor ?? ""} ${c.segmento ?? ""}`
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
  if (/segur|previd|capitaliza/.test(t)) return "insurer";
  if (/banco|intermedi|financeir/.test(t)) return "bank";
  if (/utilidade|energia|saneamento|agua/.test(t)) return "utility";
  if (/materiais basicos|petroleo|minera|mineral|siderur|metalur/.test(t)) {
    return "commodity";
  }
  return "industry";
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
