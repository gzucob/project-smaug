# project-smaug

Ferramenta pessoal de análise da carteira de ações. **Fase 1: ingestão fiel e
auditável** dos dados fundamentais (CVM/brapi), persistidos em MongoDB como um
espelho cru — sem cálculo, sem interpretação. **Fase 2: análise** — indicadores
fundamentalistas e de mercado derivados do espelho, persistidos em PostgreSQL e
servidos por uma API de leitura. Ambas já estão implementadas — veja
[Status](#status).

> A fase seguinte (análise qualitativa por critérios/IA — "tese azedando")
> ainda **não** foi implementada.

## Stack

- Python 3.13 · [uv](https://docs.astral.sh/uv/)
- Fase 1: MongoDB (Docker) + Beanie (ODM tipado) · `pycvm` para o parsing dos
  arquivos da CVM. Gatilho é **CLI** (`smaug.ingest`, `smaug.report`).
- Fase 2: PostgreSQL + SQLAlchemy + Alembic (dados derivados). Gatilho de
  cálculo é **CLI** (`smaug.analyze`); FastAPI (`smaug.entrypoints.api`) só lê
  o que já foi persistido — não recalcula nada por request.
- mypy strict · ruff · pytest

## Documentação

- Plano da Fase 1 — [`docs/PLANO_FASE1.md`](docs/PLANO_FASE1.md)
- Critérios da Fase 1 — [`docs/preview_fase1_criterios_implementacao.md`](docs/preview_fase1_criterios_implementacao.md)
- Achados de fidelidade dos indicadores (Fase 2) — [`docs/FINDINGS_INDICATORS.md`](docs/FINDINGS_INDICATORS.md)

## Setup local

```bash
uv sync                 # dependências + venv (baixa o Python 3.13)
cp .env.example .env    # preencha o BRAPI_TOKEN (só necessário se INGESTION_SOURCE=brapi)
docker compose up -d    # sobe Mongo (Fase 1) + Postgres (Fase 2)
```

## Uso (Fase 1)

```bash
# Coleta o espelho cru das 9 ações (ou de tickers específicos com -t):
uv run python -m smaug.ingest
uv run python -m smaug.ingest -t PETR4 -t VALE3

# Relatório de completude por ticker (lê o espelho, não recoleta):
uv run python -m smaug.report
```

A coleta é **append-only e re-executável com segurança**: cada chamada grava um
novo documento em `raw_ingestions` (`ticker + module + fetched_at`), preservando
o histórico de revisões. O token vai só no `.env`; nunca é persistido nem
aparece no relatório. Falha em um ticker/módulo não derruba os demais (401 e
limite de plano param a coleta; 404 pula a chamada).

### Fonte de dados (`INGESTION_SOURCE`)

A ingestão tem **duas fontes alternáveis por config**, ambas plugadas na mesma
porta `RawDataSource` — trocar é mudar uma linha no `.env`, sem reescrever código:

- `INGESTION_SOURCE=cvm` (**padrão**) — dados abertos da CVM. Baixa o ZIP anual
  do ITR (`CVM_YEAR`, default 2024), parseia com a `pycvm` e espelha os
  statements crus (`BPA`/`BPP`/`DRE`/`DFC`). Cobre a carteira inteira, inclusive
  bancos e seguradoras (no formato regulado deles). Não requer token. O mapa
  ticker → código CVM vive em `portfolio/domain/cvm_codes.py`.
- `INGESTION_SOURCE=brapi` — API da brapi. No plano gratuito só PETR4/VALE3
  retornam (as demais dão 403 e são puladas). Requer `BRAPI_TOKEN`.

## Uso (Fase 2 — indicadores)

A Fase 2 calcula os indicadores (contábeis + de mercado) a partir do espelho
CVM e da cotação do brapi, persiste no PostgreSQL e serve por FastAPI.

```bash
# 1. Sobe Mongo + Postgres:
docker compose up -d

# 2. Cria o schema derivado (uma vez):
uv run alembic upgrade head

# 3. Calcula e persiste os indicadores das 9 (ou -t TICKER):
uv run python -m smaug.analyze

# 4. Serve a API para o front-end:
uvicorn smaug.entrypoints.api:app --reload
#   GET /analysis           -> últimas análises de todas as ações
#   GET /analysis/{ticker}  -> ex.: /analysis/PETR4
```

- **Indicadores**: ROE, ROA, margens, dívida líquida/EBITDA, liquidez,
  crescimento, P/L, P/VP, EV/EBITDA, DY. Cientes de setor — bancos/seguradoras
  retornam `null` nos que não se aplicam (dívida líquida, EV/EBITDA, liquidez).
- **Unidades**: os valores da CVM (em milhares) são escalados para reais antes
  de cruzar com o preço, para os múltiplos de mercado saírem corretos.
- **Preço**: vem do brapi (`BRAPI_TOKEN`); no plano grátis só PETR4/VALE3
  retornam cotação — as demais ficam com os múltiplos de mercado nulos, mas os
  indicadores contábeis são calculados normalmente.
- **Crescimento**: precisa de ≥2 anos no espelho; rode `CVM_YEAR=2023 uv run
  python -m smaug.ingest` (e 2022) para popular histórico.
- Ainda **sem critérios de "tese azedando"** — isso fica para a fase de análise
  com IA (LangGraph/RAG).

## Estrutura

```
src/smaug/
├── ingestion/     # fontes (brapi | CVM) + persistência do espelho cru (Mongo)
├── analysis/      # cálculo de indicadores + persistência derivada (Postgres)
├── portfolio/     # mapas de referência: ticker -> setor, ticker -> código CVM
├── shared/        # config, conexões Mongo/Postgres, EventBus
└── entrypoints/   # CLI + API FastAPI
```

## Status

✅ **Fase 1** — duas fontes alternáveis por config (CVM e brapi) sob a mesma
porta, persistência do espelho cru (append-only), EventBus, CLI de coleta e
relatório de completude. A CVM coleta as 9 ações de fato, sem custo nem token.

✅ **Fase 2 implementada** — cálculo de indicadores fundamentalistas (contábeis
+ de mercado) a partir do espelho CVM + cotação brapi, persistidos em PostgreSQL
(SQLAlchemy/Alembic) e servidos por FastAPI (`GET /analysis`). Cálculo próprio,
tipado e ciente de setor. Falta a fase de análise qualitativa por IA (critérios).
