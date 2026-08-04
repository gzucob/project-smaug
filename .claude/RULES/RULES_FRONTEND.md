---
description: Front-end (Next.js) location, stack, "Smaug" design system, data boundary, and dev workflow
applies_to: frontend/**/*.{ts,tsx,css}
---

# Front-end Rules

The front-end lives in **`frontend/`** at the repo root — a separate app from
the Python backend under `src/smaug/`. It is a read-only UI over the Phase 2
FastAPI analysis API; it never computes indicators, only fetches and formats
already-computed results. The one write it makes is favoriting/un-favoriting a
ticker (#151) — a preference, not a computation; `CLAUDE.md`'s "the API stays
read-only" is about *indicators*, and that boundary is untouched.

## Stack

- **Next.js 15** (App Router, Server Components by default) · **React 19** ·
  **Tailwind CSS v4** · **TypeScript 5**.
- Tailwind v4 is **CSS-first**: there is no `tailwind.config`. All design
  tokens live in `@theme` inside `src/app/globals.css`; the PostCSS plugin is
  wired in `postcss.config.mjs`.
- Fonts come through `next/font/google` (self-hosted at build). Icons come from
  **`react-icons`** — do not hand-roll SVG icons. Overlays and menus come from
  **`@headlessui/react`** (unstyled behaviour; the look is ours).
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

### Colour means direction; identity is a detail

`--color-up` (blue) and `--color-down` (red) carry **direction where there is
movement to see**: every data mark in a chart, and the favourable/treacherous
markers in the indicator docs. Blue and red rather than green and red on purpose
— protanopia and deuteranopia are a red/green confusion, so the usual financial
palette is the one pair that fails them.

**A grid cell is a value, not a movement, and always takes the neutral ink** —
including the growth and compounded-growth cells. They used to be sign-coloured
(`IndicatorSpec.signed`, now gone), which read fine only because the signed cells
on screen happened to be negative; the moment the CAGRs landed (#144) the grid
grew four bright blue values among thirty cream ones and they read as alerts
rather than as numbers. The sign is already in the glyph — `signedPct` writes the
`+`/`−` — so colour on top of it encoded the same fact twice while competing with
the group accent.

The same applies to the drill-down's headline value: the chart beneath it is
where its direction is coloured.

### Gemstone-per-sector encoding

Each of the five sectors owns one vivid hue (`--color-gem-azure` = bank,
`-violet` = insurer, `-jade` = utility, `-gold` = commodity, `-coral` =
industry), mapped in `src/lib/sectors.ts`.

**Its territory is the details, not the data** (#145): the classification badge,
the ticker card, a heading accent, the chart's average reference line. Charts
used to take the sector hue, so an insurer's page was violet throughout — which
answered a question nobody asks (the reader knows which company they opened)
while the question the chart does raise, up or down, had no colour at all.

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
  client-side calls to the FastAPI base URL. The one deliberate exception is
  the favorite-ticker toggle (`FavoriteButton`, #151, ADR 0049): a click has
  nowhere else to originate from, so it calls this app's own same-origin
  `app/api/portfolio/[ticker]/route.ts`, which is what proxies to FastAPI —
  the browser itself never gains a cross-origin surface. A new mutation
  follows the same shape; it does not get its own reason to call FastAPI
  directly from the client.
- **The ticker page carries one indicator grid**, the twelve-month window. It
  used to stack a second, identical grid for the latest closed exercise;
  comparing 29 numbers by eye is not a comparison (#32).
- **The change against that exercise lives in the drill-down**, as a stat naming
  both ends (`9,6% → 7,5%`), not in the cell. A bare `▼ 2,1 p.p.` under a value
  states a magnitude without saying what it was measured against.
- **The drill-down's left column is the reading, the right column is tabbed.**
  The value leads at full size; the references (average, min·max) are one quiet
  line, not a card each — four cards competed with the number they exist to
  qualify. The right column splits by the question being asked: *Entenda* (what
  it means) and *Como calculamos* (formula and where our arithmetic departs).
  New material goes in a tab, not stacked on: the chart must stay readable
  alongside "onde engana", which is what a single scrolling column loses.
- **A delta is stated in the unit the reader thinks in** (`deltaText`): a ratio
  shown as `%` moves in **percentage points**, a multiple in `×`, money in
  relative terms. A delta that rounds away at the shown precision is not
  rendered — "▼ 0,00×" points somewhere and says nothing.
- **A delta is never sign-coloured.** An arrow states direction, which is a
  fact; green would state "good", which is the judgement the domain refuses to
  make — and a rising P/L is not the same news as a rising ROE.
- **A screen never shows a statement slice without naming it** (ADR 0026). A
  bare indicator name is the controllers' slice; `roe_total` and friends are the
  consolidated group. `lib/indicators.ts` owns the pairing (`basisPair`,
  `BASIS_LABEL`) and the drill-down carries the toggle. The second basis is
  surfaced in a cell **only when the two format differently** — comparing the
  rendered text, not a tolerance, because a line that repeats "24,2%" costs
  height and says nothing.
- **A null is never explained by the front-end.** The API sends `null_reasons`
  (ADR 0008) naming why each indicator is null; render it through
  `lib/null-reasons.ts` and distinguish a deliberate n/d (`inapplicable_regime`,
  `zero_denominator`) from a gap of ours (missing price, unmapped account),
  which is coloured as the warning it is. The old `naSectors` field mirrored the
  calculator's guards by hand and was deleted in #54 — do not reintroduce a
  second source for a fact the API already states.
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
- **Overlays and menus come from `@headlessui/react`; the styling is ours.**
  A native `<select>` is out — its dropdown is drawn by the operating system,
  unreachable from CSS, foreign on this panel (#143) — and so is hand-rolling
  the behaviour: we wrote a focus trap, a portal, a scroll lock and arrow
  handling twice before, and the hand-rolled list still hung inside a scrolling
  column where a longer list would have been clipped.
  - `Dialog`/`DialogPanel` for the modal, `Listbox` for a menu; see
    `IndicatorDetail` and `IndicatorPicker`.
  - `anchor` positions a floating list **and portals it**, which is what keeps
    it out of an `overflow` ancestor. Bound it with `[--anchor-max-height:…]`:
    the anchor writes a height inline from the space available, and inline beats
    a class, so a `max-h-*` utility is silently ignored.
  - Style through the `data-*` attributes it exposes (`data-focus`,
    `group-data-open`), never by re-implementing the state.
- **Dynamic (per-sector) colours use inline `style` with `var(--color-…)`**,
  since Tailwind cannot know a runtime value; static colours use utilities.
- The brand mark is **`DragonMark`** (react-icons `GiSpikedDragonHead`,
  molten-gold gradient + a restrained ember glow). Reuse it; don't inline other
  dragon SVGs.
- **No attention-grabbing UI.** No pulsing "live" badges. The analysis-view
  label is a calm pill: `TTM · 12 meses` / `Exercício {year}` (`ViewBadge`).
- **Any overlay renders through `createPortal` into `document.body`.**
  `position: fixed` anchors to the nearest *transformed* ancestor rather than to
  the window, and this app has two on the ticker page — `.rise` (whose `forwards`
  fill keeps `translateY(0)` applied after the animation ends) and `.panel-hover`
  while the pointer is on a card. In place, the indicator modal inherited the
  card's box instead of the viewport's, pushing its own top out of reach with
  nothing left to scroll. A portal is the fix; do not chase it with z-index.
- A modal that can outgrow the screen caps its height and lets its **content**
  scroll, never the page behind it (the body is locked while it is open). Under
  a CSS grid that means `grid-rows-[minmax(0,1fr)]` plus `min-h-0` on the
  scrolling child — an `auto` row is sized by its content, overshoots the cap
  and is clipped with no scrollbar anywhere.

## Charts

A number only means something against a ruler. Any chart that a reader is meant
to *read* — not merely glance at — carries a value axis, grid lines, the zero
baseline and, where it exists, a reference the series is compared to (the
asset's own historical average). **A miniature without a scale is decoration**:
a sparkline inside a dense indicator cell was tried and rejected in #31 — a
22px stroke with no axis and no reference told the reader nothing that the
number above it did not.

- **`recharts`** draws every chart, through the single `IndicatorChart`
  component — the drill-down and the ticker page's annual cards alike. It costs
  ~115 kB on the ticker page's first load; that is the price of a readable
  scale, paid once.
- `IndicatorChart` is a Client Component and `HistoryCharts` a Server one, so
  the formatter crosses that boundary **by name** (`FormatKind` +
  `axisFormatter`), never as a function — React cannot serialize one, and the
  page 500s if you try.
- **Never hand a formatter straight to a Recharts `tickFormatter`.** It calls
  back with `(value, index)`, and this project's formatters take
  `(value, digits)` — the index lands in `digits` and each tick down the axis
  grows a decimal ("0%", "20,0%", "40,00%"). Wrap it in a one-argument lambda.
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
- `Sparkline` (hand-rolled SVG) survives in `HistoryStrip` only: a trend line
  next to a headline figure is a different job from a chart meant to be read.
  `YearBars` is gone — #34 replaced it everywhere.

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
