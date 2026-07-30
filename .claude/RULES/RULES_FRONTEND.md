---
description: Front-end (Next.js) location, stack, "Smaug" design system, data boundary, and dev workflow
applies_to: frontend/**/*.{ts,tsx,css}
---

# Front-end Rules

The front-end lives in **`frontend/`** at the repo root — a separate app from
the Python backend under `src/smaug/`. It is a read-only UI over the Phase 2
FastAPI analysis API; it never computes indicators, only fetches and formats
already-computed results.

## Stack

- **Next.js 15** (App Router, Server Components by default) · **React 19** ·
  **Tailwind CSS v4** · **TypeScript 5**.
- Tailwind v4 is **CSS-first**: there is no `tailwind.config`. All design
  tokens live in `@theme` inside `src/app/globals.css`; the PostCSS plugin is
  wired in `postcss.config.mjs`.
- Fonts come through `next/font/google` (self-hosted at build). Icons come from
  **`react-icons`** — do not hand-roll SVG icons.
- Charts come from **`recharts`**, and only where a reading needs a real scale
  (see *Charts* below).

Restate this stack before proposing a new dependency or restructuring.

## Design system — "Smaug", minimalist

- **Flat, warm near-black surface. No background atmosphere.** Do not add
  full-page glows, gradient meshes, or grid overlays — they were deliberately
  removed. The background is a single solid `--color-vault-950`.
- **Vivid color is reserved for details only** (badges, accents, data marks,
  values, the wordmark). Color must always carry meaning, never decorate a
  background.
- **Design tokens are defined once** in the `@theme` block of `globals.css`
  (`--color-vault-*`, `--color-ink-*`, `--color-gold-*`, `--color-ember-*`,
  `--color-gem-*`, `--color-up/down`, and the `--font-*` families). Add a new
  token there and consume it via a Tailwind utility (`bg-gold-500`) or
  `var(--color-…)` — never hard-code a hex value in a component.
- **Panels are flat**: the `.panel` utility is a solid fill + a neutral
  hairline border, no heavy shadow or backdrop blur. `.panel-hover` adds only a
  small lift + border brighten.

### Gemstone-per-sector encoding

Each of the five sectors owns one vivid hue (`--color-gem-azure` = bank,
`-violet` = insurer, `-jade` = utility, `-gold` = commodity, `-coral` =
industry), mapped in `src/lib/sectors.ts`.

- The gem **name** (Safira, Ametista, Esmeralda, Ouro, Rubi) is an **internal
  reference only** — it explains *why* a sector has its colour. **Never render
  the gem name in the UI.** Screens show the sector label + its colour, nothing
  more. Keep the `gem` field in `sectors.ts` as documentation of intent.

## Typography

Modern / corporate tone (Anthropic-adjacent), while the **RPG theme stays** in
the imagery and vocabulary (dragon mark, gold, gems, "toca do dragão" copy) —
not in the fonts.

- `--font-body` / `--font-brand` → **Geist** (UI, body, and the `SMAUG`
  wordmark).
- `--font-display` → **Newsreader** (sober editorial serif — headings only).
- `--font-mono` → **Geist Mono**, exposed through the **`.nums`** utility.
  **All numeric / financial data uses `.nums`** for tabular figures.
- Do not reintroduce fantasy display faces (Cinzel/Fraunces were dropped).

## Data boundary

- **Fetch server-side, in Server Components**, through `src/lib/api.ts`. The
  base URL is `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`). Because
  fetching is server-side there is **no CORS surface** — do not add
  client-side calls to the API.
- `lib/api.ts` returns a **non-throwing `ApiResult` discriminated union**.
  Pages must render the `VaultOffline` empty state on `ok: false` (backend down
  or 404) instead of throwing.
- `src/lib/types.ts` mirrors the API response models. API decimals arrive as a
  **JSON string or number** — always coerce with **`toNum()`** (`lib/format.ts`)
  before any arithmetic; never operate on the raw value.
- **Formatting is the front-end's job.** The domain sends ratios as fractions
  (`0.18` = 18%); `lib/format.ts` multiplies for `%`, renders multiples (`×`),
  and money (`R$`, compact) — all in **PT-BR** (comma decimals). Per-indicator
  display metadata (label, group, formatter) lives in `lib/indicators.ts`.

## Components

- **Server-first.** Add `"use client"` only where interactivity is required
  (e.g. `TickerSearch`, which navigates on submit — no client-side data fetch).
- **Dynamic (per-sector) colours use inline `style` with `var(--color-…)`**,
  since Tailwind cannot know a runtime value; static colours use utilities.
- The brand mark is **`DragonMark`** (react-icons `GiSpikedDragonHead`,
  molten-gold gradient + a restrained ember glow). Reuse it; don't inline other
  dragon SVGs.
- **No attention-grabbing UI.** No pulsing "live" badges. The analysis-view
  label is a calm pill: `TTM · 12 meses` / `Exercício {year}` (`ViewBadge`).

## Charts

A number only means something against a ruler. Any chart that a reader is meant
to *read* — not merely glance at — carries a value axis, grid lines, the zero
baseline and, where it exists, a reference the series is compared to (the
asset's own historical average). **A miniature without a scale is decoration**:
a sparkline inside a dense indicator cell was tried and rejected in #31 — a
22px stroke with no axis and no reference told the reader nothing that the
number above it did not.

- **`recharts`** draws the indicator drill-down (`IndicatorChart`). It is
  imported through **`next/dynamic`** from `IndicatorDetail`, because ~115 kB
  is a third of the ticker page and nobody needs it until a modal opens.
- Colours, fonts and grid strokes come from the same `@theme` tokens as
  everything else — pass `var(--color-…)` into Recharts props; never a hex.
- **Recharts' own animations stay off** (`isAnimationActive={false}`): they run
  well past the 300ms UI budget and sit outside the `prefers-reduced-motion`
  CSS guard, which cannot reach them.
- The value axis snaps to round 1/2/2.5/5 steps and always spans zero
  (`axisScale` in `IndicatorChart`). A bar cut off below its baseline
  overstates the variation.
- **A TTM window is never drawn as one more closed exercise**: it keeps a
  hollow/dashed basis of its own, and it is excluded from the average, the
  min/max and any other statistic over the exercises.
- The hand-rolled SVG charts (`YearBars`, `Sparkline`, `HistoryCharts`,
  `HistoryStrip`) still serve the ticker page's overview strips. Leave them —
  a small trend next to a headline figure is a different job from a drill-down.

## Motion

Minimal. A subtle `.rise` entrance (staggered via inline `animationDelay`, 260ms
on the `--ease-out-strong` curve) is the only page-load motion; the indicator
modal adds an entry-only fade + `scale(0.97)` settle. Curves and durations are
**tokens** in the `@theme` block (`--ease-out-strong`, `--duration-*`) — never
hand-type a `cubic-bezier` or a duration in a component, and keep every UI
duration under 300ms.

The `prefers-reduced-motion` guard in `globals.css` **drops movement, not all
motion**: it removes translates, scales and the stagger, and deliberately keeps
colour/opacity transitions, which aid comprehension rather than decorate. Keep
new motion inside that guard. Gate hover *movement* behind
`@media (hover: hover) and (pointer: fine)` (or Tailwind's `motion-safe:`) —
touch fires a synthetic hover that then sticks. No decorative background
animation.

## Dev workflow & quality gate

- Before committing front-end changes: **`npm run typecheck`** and
  **`npm run build`** must both pass.
- **Never run `npm run build` while `npm run dev` is running.** Both write
  `.next/`; the build clobbers the dev server's chunks and yields
  `MODULE_NOT_FOUND` / HTTP 500. Stop the dev server first (or delete `.next`
  and restart) — the code is fine, the cache is not.
- Copy `.env.local.example` → `.env.local` to point at the backend.

## Language

UI-facing text is **PT-BR** (user-facing convention). Identifiers, comments,
commit messages, and PRs are **English** — same as the project-wide rule in
`CLAUDE.md`.
