# project-smaug

Ferramenta pessoal de análise da carteira de ações. **Fase 1: ingestão fiel e
auditável** dos dados fundamentais (CVM/B3), persistidos em MongoDB como um
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

- Roadmap (objetivo e milestones M0–M3) — [`docs/ROADMAP.md`](docs/ROADMAP.md)
- Decisões de arquitetura e modelagem (ADRs) — [`docs/adr/`](docs/adr/)
- Modelo de documentação (regra vs. ADR vs. issue vs. relatório gerado) —
  [`docs/AGENTS.md`](docs/AGENTS.md)

O que é verdade sobre os dados **agora** não vive em documento: vem de um
comando (`smaug doctor`) e dos testes. Os planos da Fase 1 e o log de achados
(`FINDINGS_INDICATORS.md`) foram aposentados — suas decisões viraram ADRs, seus
follow-ups viraram issues. O histórico segue no git.

## Setup local

```bash
uv sync                 # dependências + venv (baixa o Python 3.13)
cp .env.example .env    # nada a preencher: nenhuma fonte pede credencial
docker compose up -d    # sobe Mongo (Fase 1) + Postgres (Fase 2)
```

## Uso (Fase 1)

O entrypoint é a CLI Typer `smaug.entrypoints.cli`; com o pacote instalado
(`uv sync`) os comandos também respondem pelo atalho `smaug <comando>`.

```bash
# Coleta o espelho cru das 9 ações (ou de tickers específicos com -t):
uv run python -m smaug.entrypoints.cli ingest
uv run python -m smaug.entrypoints.cli ingest -t PETR4 -t VALE3

# Relatório de completude por ticker (lê o espelho, não recoleta):
uv run python -m smaug.entrypoints.cli report
```

A coleta é **append-only e re-executável com segurança**: cada chamada grava um
novo documento em `raw_ingestions` (`ticker + module + fetched_at`), preservando
o histórico de revisões. Falha em um ticker/módulo não derruba os demais (um
erro fatal para a fonte para a coleta; 404 pula a chamada).

### Fontes de dados

Duas, ambas públicas e sem autenticação (ADR 0041):

- **CVM** — dados abertos. Baixa o ZIP anual (`CVM_YEAR`, default 2024) e
  espelha os statements crus (`BPA`/`BPP`/`DRE`/`DFC`/…), mais as contagens de
  ações do FRE. Um arquivo é lido uma vez e serve a bolsa inteira. O mapa
  ticker → código CVM vive em `portfolio/domain/cvm_codes.py`.
- **B3** — os eventos societários e os dividendos que a bolsa publica
  (`CAPITAL_EVENT_B3`, `CASH_DIVIDEND_B3`), e a série de cotações
  (`COTAHIST_A{ano}.ZIP`), que é a única fonte de preço da Fase 2.

## Uso (Fase 2 — indicadores)

A Fase 2 calcula os indicadores (contábeis + de mercado) a partir do espelho
CVM e da série de cotações da B3, persiste no PostgreSQL e serve por FastAPI.

```bash
# 1. Sobe Mongo + Postgres:
docker compose up -d

# 2. Cria o schema derivado (uma vez):
uv run alembic upgrade head

# 3. Calcula e persiste os indicadores das 9 (ou -t TICKER):
uv run python -m smaug.entrypoints.cli analyze

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
- **Preço**: a série histórica que a **própria B3 publica**
  (`COTAHIST_A{ano}.ZIP` — sem token, um arquivo por ano desde 1986, ADR 0032),
  e nada mais (ADR 0041). A B3 publica o preço **como negociado**, então ele é
  dividido pelos eventos societários posteriores a cada pregão para casar com a
  base acionária restatada da ADR 0027 (ADR 0033), e os dividendos entram de
  volta numa terceira base (ADR 0039). Um papel sem nenhum pregão no período
  fica com os múltiplos de mercado nulos, com a causa nomeada; os indicadores
  contábeis saem normalmente.
- **Crescimento**: precisa de ≥2 anos no espelho; rode `CVM_YEAR=2023 uv run
  python -m smaug.entrypoints.cli ingest` (e 2022) para popular histórico.
- Ainda **sem critérios de "tese azedando"** — isso fica para a fase de análise
  com IA (LangGraph/RAG).

## Estrutura

```
src/smaug/
├── ingestion/     # leitores CVM/B3 + persistência do espelho cru (Mongo)
├── analysis/      # cálculo de indicadores + persistência derivada (Postgres)
├── portfolio/     # mapas de referência: ticker -> setor, ticker -> código CVM
├── shared/        # config, conexões Mongo/Postgres, EventBus
└── entrypoints/   # CLI + API FastAPI
```

## Status

✅ **Fase 1** — leitura da CVM e da B3 sob a mesma porta `RawDataSource`,
persistência do espelho cru (append-only), EventBus, CLI de coleta e relatório
de completude. Roda sobre a bolsa inteira, sem custo e sem credencial.

✅ **Fase 2 implementada** — cálculo de indicadores fundamentalistas (contábeis
+ de mercado) a partir do espelho CVM + série de cotações da B3, em PostgreSQL
(SQLAlchemy/Alembic) e servidos por FastAPI (`GET /analysis`). Cálculo próprio,
tipado e ciente de setor. Falta a fase de análise qualitativa por IA (critérios).
