/**
 * Presentation formatting. The API delivers ratios as fractions (0.18 = 18%),
 * so percentage helpers multiply by 100 here — the domain never pre-formats.
 * All output is PT-BR (comma decimals, `R$`).
 */
import type { Decimalish } from "@/lib/types";

const EN_DASH = "–";

export function toNum(v: Decimalish | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

const nf = (min: number, max: number) =>
  new Intl.NumberFormat("pt-BR", {
    minimumFractionDigits: min,
    maximumFractionDigits: max,
  });

/** Fraction → percent, e.g. 0.184 → "18,4%". */
export function pct(v: Decimalish, digits = 1): string {
  const n = toNum(v);
  if (n === null) return EN_DASH;
  return `${nf(digits, digits).format(n * 100)}%`;
}

/** Signed fraction → percent with an explicit +/−, for growth figures. */
export function signedPct(v: Decimalish, digits = 1): string {
  const n = toNum(v);
  if (n === null) return EN_DASH;
  const sign = n > 0 ? "+" : "";
  return `${sign}${nf(digits, digits).format(n * 100)}%`;
}

/** A ratio rendered as a multiple, e.g. 8.24 → "8,24×". */
export function multiple(v: Decimalish, digits = 2): string {
  const n = toNum(v);
  if (n === null) return EN_DASH;
  return `${nf(digits, digits).format(n)}×`;
}

/** Per-share price, e.g. "R$ 38,20". */
export function price(v: Decimalish): string {
  const n = toNum(v);
  if (n === null) return EN_DASH;
  return `R$ ${nf(2, 2).format(n)}`;
}

/** Large monetary values in compact BRL, e.g. "R$ 1,24 bi". */
export function money(v: Decimalish): string {
  const n = toNum(v);
  if (n === null) return EN_DASH;
  const abs = Math.abs(n);
  const sign = n < 0 ? "−" : "";
  const scale = (div: number, suffix: string) =>
    `${sign}R$ ${nf(0, 2).format(abs / div)} ${suffix}`;
  if (abs >= 1e12) return scale(1e12, "tri");
  if (abs >= 1e9) return scale(1e9, "bi");
  if (abs >= 1e6) return scale(1e6, "mi");
  if (abs >= 1e3) return scale(1e3, "mil");
  return `${sign}R$ ${nf(0, 2).format(abs)}`;
}

/** Sign of a fraction for coloring; 0/null → "flat". */
export function signOf(v: Decimalish): "up" | "down" | "flat" {
  const n = toNum(v);
  if (n === null || n === 0) return "flat";
  return n > 0 ? "up" : "down";
}

/** ISO date → closed-year label ("2024") or month/year for a live period. */
export function yearOf(iso: string): string {
  return iso?.slice(0, 4) ?? EN_DASH;
}

export function monthYear(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("pt-BR", {
    month: "short",
    year: "numeric",
  }).format(d);
}

export function dateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

export const DASH = EN_DASH;
