# Smaug — Frontend

Front-end for **project-smaug**: a dark "dragon's hoard" dashboard over the
Phase 2 analysis read API.

- **Stack:** Next.js 15 (App Router) · React 19 · Tailwind CSS v4 · TypeScript 5
- **Design:** *Smaug's Hoard* — warm near-black vault, molten-gold as the
  primary metal, one vivid gemstone hue per sector (safira/ametista/esmeralda/
  ouro/rubi) as a data-encoding system.
- **Fonts:** Cinzel (wordmark), Fraunces (display), Manrope (body),
  IBM Plex Mono (numeric data).

## Screens

| Route | Screen |
|---|---|
| `/` | Home / landing — branding + ticker search |
| `/portfolio` | Portfolio overview, grouped by sector |
| `/ticker/[symbol]` | Ticker detail — TTM live + closed-year, indicators, trajectory |

## Data

Server Components fetch the FastAPI read API (`smaug.entrypoints.api`) server-side
(no CORS needed). Configure the base URL in `.env.local`:

```bash
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE=http://localhost:8000
```

## Develop

```bash
npm install
npm run dev        # http://localhost:3000
```

Backend (in the repo root, separate terminal):

```bash
uvicorn smaug.entrypoints.api:app --reload
```

Quality:

```bash
npm run typecheck
npm run build
```
