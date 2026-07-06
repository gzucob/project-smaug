# project-smaug

Ferramenta pessoal de análise da carteira de ações. **Fase 1: ingestão fiel e
auditável** dos dados fundamentais via API [brapi](https://brapi.dev),
persistidos em MongoDB como um espelho cru — sem cálculo, sem interpretação.

> A Fase 2 (análise por critérios) é escopo futuro e **não** vive aqui ainda.

## Stack

- Python 3.13 · [uv](https://docs.astral.sh/uv/)
- FastAPI (esqueleto do sistema) · o gatilho da Fase 1 é **CLI**
- MongoDB (Docker) + Beanie (ODM tipado)
- mypy strict · ruff · pytest

## Documentação

- Plano da Fase 1 — [`docs/PLANO_FASE1.md`](docs/PLANO_FASE1.md)
- Critérios da Fase 1 — [`docs/preview_fase1_criterios_implementacao.md`](docs/preview_fase1_criterios_implementacao.md)

## Setup local

```bash
uv sync                 # dependências + venv (baixa o Python 3.13)
cp .env.example .env    # preencha o BRAPI_TOKEN
docker compose up -d    # sobe o MongoDB
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

- `INGESTION_SOURCE=cvm` (**padrão**) — dados abertos da CVM. Cobre a carteira
  inteira, inclusive bancos e seguradoras. **Ainda é esqueleto**: rodar `ingest`
  nesta fonte falha com uma mensagem clara pedindo `INGESTION_SOURCE=brapi`.
- `INGESTION_SOURCE=brapi` — API da brapi. Funcional hoje; no plano gratuito só
  PETR4/VALE3 retornam (as demais dão 403 e são puladas). Requer `BRAPI_TOKEN`.

## Estrutura

```
src/smaug/
├── ingestion/     # busca na brapi + persistência do espelho cru
├── portfolio/     # mapa ticker -> setor (referência)
├── shared/        # config, conexão Mongo, EventBus
└── entrypoints/   # CLI
```

## Status

✅ **Fase 1 implementada** — cliente brapi, persistência do espelho cru
(append-only), EventBus in-process, CLI de coleta e relatório de completude
com verificação setorial. Falta a **coleta real** das 9 ações (rodar com o
token) e a inspeção do relatório para confirmar a fundação da Fase 2.
