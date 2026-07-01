# project-smaug

Ferramenta pessoal de análise da carteira de ações. Fase 1: ingestão fiel dos
dados fundamentais da brapi em MongoDB (espelho cru, sem cálculo). A Fase 2
(análise por critérios) é escopo futuro e não vive aqui ainda.

## Stack
- Python 3.13 · uv · FastAPI (esqueleto; o gatilho da Fase 1 é CLI)
- MongoDB (Docker) + Beanie · mypy strict · ruff · pytest
- PostgreSQL + SQLAlchemy + Alembic: deferidos para a Fase 2 (dados derivados)

Sempre reafirme a stack antes de propor arquitetura ou dependências.

## Fonte de verdade
- O código é a verdade do comportamento implementado.
- `docs/PLANO_FASE1.md` — o "como" da Fase 1.
- `docs/preview_fase1_criterios_implementacao.md` — o "o quê / porquê".
- `.claude/RULES/` — regras duráveis de engenharia.

## Índice de regras
| Arquivo | Cobre |
|---|---|
| `.claude/RULES/RULES_BRANCHES.md` | Branch, squash-merge, fluxo a partir da main |
| `.claude/RULES/RULES_ISSUES.md` | Formato `[NAMESPACE-NN]`, labels area/priority/type |
| `.claude/RULES/RULES_GIT_WORKFLOW.md` | Gate de qualidade, commit, push, PR |

## Arquitetura (DDD Lite)
Contextos isolados sob `src/smaug/`: `ingestion`, `portfolio`, `shared`,
`entrypoints`. Camadas: domain → application → infrastructure → entrypoints.
Comunicação entre contextos só por eventos (EventBus in-process).

## O que NÃO fazer
- Não fazer push direto na `main` — sempre branch + PR + squash.
- Não commitar segredos — token da brapi só no `.env` (ignorado). Repo é público.
- Não escrever lógica de negócio em entrypoints (CLI/API) — eles chamam casos de uso.
- Não colocar cálculo/indicador na Fase 1 (isso é Fase 2).
- Código, commits e PRs em inglês.
