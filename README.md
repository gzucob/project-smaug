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

## Estrutura

```
src/smaug/
├── ingestion/     # busca na brapi + persistência do espelho cru
├── portfolio/     # mapa ticker -> setor (referência)
├── shared/        # config, conexão Mongo, EventBus
└── entrypoints/   # CLI
```

## Status

🚧 Esqueleto inicial — nenhuma lógica de coleta implementada ainda.
