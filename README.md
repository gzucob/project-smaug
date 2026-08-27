# project-smaug

Ferramenta pessoal de análise fundamentalista de ações da B3. O projeto é
organizado em duas fases:

- **Fase 1 — ingestão:** baixa dados públicos da CVM e da B3 e mantém um
  espelho cru, fiel e auditável em MongoDB. Essa fase não calcula indicadores
  nem interpreta os dados.
- **Fase 2 — análise:** lê o espelho e a série histórica da B3, calcula
  indicadores fundamentalistas e de mercado, persiste os resultados em
  PostgreSQL e os disponibiliza por uma API FastAPI.

O front-end Next.js consome a API e apresenta as análises em TTM e exercícios
fechados. A análise qualitativa por critérios/IA ainda não foi implementada.

## Fluxo

```text
Arquivos CVM + arquivos B3
          │
          ▼
  smaug ingest  ───────────────► MongoDB
                                      │
                                      ▼
  B3 COTAHIST + espelho CVM ──► smaug analyze ──► PostgreSQL
                                                       │
                                                       ▼
                                           FastAPI ──► Next.js
```

O cálculo e a persistência dos indicadores acontecem exclusivamente no comando
`analyze`. A API lê análises já persistidas; a única escrita adicional da API é
a preferência de favoritar ou desfavoritar um ticker.

## Stack

### Backend

- Python 3.13 e [uv](https://docs.astral.sh/uv/)
- FastAPI e Uvicorn
- Typer para a CLI
- MongoDB 8 + Beanie para o espelho cru
- PostgreSQL 17 + SQLAlchemy + Alembic para os dados derivados
- Pydantic Settings para configuração
- Ruff, mypy strict e pytest

### Front-end

- Next.js 15 com App Router e Server Components
- React 19
- TypeScript 5
- Tailwind CSS v4
- Recharts para gráficos

Os arquivos da CVM são lidos diretamente como CSV. `pycvm` não é uma
dependência do projeto.

## Fontes e regras de dados

Todas as fontes são públicas e não exigem autenticação.

- **CVM:** arquivos anuais DFP, arquivos trimestrais ITR, FRE, FCA e demais
  registros necessários para demonstrações, contagens de ações, identidade e
  classes de negociação.
- **B3:** série histórica `COTAHIST_A{ano}.ZIP`, eventos societários,
  dividendos e classificação setorial.

As fronteiras de autoridade são deliberadas:

- A CVM é a fonte dos documentos e dados contábeis.
- A B3 é a única fonte de preços.
- Yahoo Finance, brapi e outros agregadores não são fallback, fixture nem
  critério de aceitação.
- Preços como negociados, preços ajustados por eventos societários e preços
  ajustados por dividendos são bases diferentes e não são misturados.
- A capitalização soma cada classe listada com seu próprio preço e considera
  apenas ações em circulação, descontando ações em tesouraria.
- Dados ausentes permanecem `null` com uma causa nomeada; o sistema não infere
  zero, dívida inexistente ou preço de outra fonte.

As decisões de modelagem e proveniência estão registradas em
[`docs/adr/`](docs/adr/). Em particular, o [ADR 0009](docs/adr/0009-read-cvm-statement-csvs-directly.md)
documenta a remoção do `pycvm` e a leitura direta dos CSVs da CVM.

## Pré-requisitos

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker e Docker Compose
- Node.js e npm, caso o front-end seja executado localmente

Não há credenciais para preencher: os endpoints usados pelo projeto são
públicos e não autenticados.

## Configuração e instalação

Na raiz do repositório:

```bash
uv sync
cp .env.example .env
docker compose up -d
uv run alembic upgrade head
```

O `.env.example` contém os valores padrão. Os principais parâmetros são:

- `CVM_DOCUMENT`: `DFP` para exercícios anuais fechados ou `ITR` para dados
  trimestrais. O padrão é `DFP`.
- `CVM_YEAR`: ano do arquivo CVM usado por uma coleta sem `--year`. O padrão é
  `2024`.
- `CVM_FCA_YEAR`: snapshot FCA completo que define a identidade atual e o
  universo listado. É independente de `CVM_YEAR`; o padrão é `2026`.
- `CVM_MODULES`: módulos CVM/B3 a coletar; por padrão inclui demonstrações,
  capital, eventos societários e dividendos.
- `MONGO_URI` e `MONGO_DB`: conexão do espelho cru.
- `POSTGRES_URI`: conexão dos indicadores derivados.
- `CVM_CACHE_DIR`, `B3_CACHE_DIR` e `SOURCE_ARTIFACT_DIR`: caches locais e
  armazenamento Bronze dos artefatos de origem.

O Docker Compose inicia:

- MongoDB em `localhost:27017`
- PostgreSQL em `localhost:5432`

## CLI

Depois de `uv sync`, a CLI pode ser chamada como `uv run smaug`. A forma
equivalente, útil quando o pacote ainda não foi instalado como script, é
`uv run python -m smaug.entrypoints.cli`.

```bash
uv run smaug --help
```

### Ingestão

`ingest` baixa os módulos configurados e grava o espelho em MongoDB.

```bash
# Escopo padrão: universo completo elegível.
uv run smaug ingest

# Escopo explícito de alguns tickers; --ticker pode ser repetido.
uv run smaug ingest --ticker PETR4 --ticker VALE3

# Arquivo anual DFP de um ano específico.
uv run smaug ingest --all --document DFP --year 2024

# Intervalo de arquivos trimestrais ITR.
uv run smaug ingest --all --document ITR --from-year 2022 --to-year 2024

# Recoleta deliberada e ajuste da concorrência dos arquivos CVM.
uv run smaug ingest --all --force --concurrency 4
```

O espelho é append-only e semanticamente idempotente: repetir a mesma fonte e
o mesmo conteúdo não cria uma nova versão; uma emenda do arquivo permanece uma
versão nova. A coleta é reexecutável, registra falhas por chamada e pode ser
retomada sem recolher indiscriminadamente o que já foi processado.

Para uma análise histórica, carregue os DFP necessários. Para TTM, carregue
também os ITRs dos trimestres necessários; a análise combina as janelas
disponíveis no espelho.

### Relatório de completude

`report` consulta o espelho cru e mostra a cobertura por módulo. Ele exige um
escopo explícito para evitar um relatório acidentalmente enorme:

```bash
uv run smaug report --ticker PETR4 --ticker VALE3
uv run smaug report --all
```

`report` não calcula indicadores e não consulta o PostgreSQL.

### Análise e cobertura

`analyze` calcula e persiste os indicadores no PostgreSQL. Sem filtro, ele
analisa o universo completo de códigos negociados; `--all` torna esse escopo
explícito.

```bash
uv run smaug analyze
uv run smaug analyze --ticker PETR4 --ticker VALE3
uv run smaug analyze --all --verbose
```

`doctor` é uma verificação somente leitura da análise persistida. Para cada
indicador, informa valor, `null` com causa nomeada ou `null` não classificado.
Ele retorna código diferente de zero quando encontra nulos não classificados.
O comando é uma verificação de cobertura, não uma prova de que toda aritmética
não nula está correta.

```bash
uv run smaug doctor --ticker PETR4
uv run smaug doctor --all
uv run smaug doctor --all --verbose
```

### Diagnóstico e manutenção

```bash
# Lista execuções de ingestão persistidas.
uv run smaug ingestion-runs --limit 10
uv run smaug ingestion-runs --run-id RUN_ID

# Inspeciona validações de lotes e, após revisão, aprova uma quarentena.
uv run smaug ingestion-validations --limit 20
uv run smaug ingestion-validations --approve VALIDATION_ID --note "revisado"

# Retoma chamadas elegíveis que falharam em uma execução anterior.
uv run smaug ingestion-resume --run-id RUN_ID
uv run smaug ingestion-resume --run-id RUN_ID --retry-permanent

# Verifica divergências da taxonomia B3; --write atualiza o snapshot versionado.
uv run smaug taxonomy
uv run smaug taxonomy --write

# Relabela documentos antigos com o registrante CVM correto.
uv run smaug relink

# Remove execuções de análise substituídas, mantendo a mais recente por célula.
uv run smaug prune
```

`taxonomy --write`, `relink` e `prune` são operações deliberadas de
manutenção. `prune` remove dados derivados antigos que já não são usados pelas
leituras atuais; não é executado automaticamente pelo `analyze`.

## Indicadores e bases de análise

A análise persistida possui duas perspectivas:

- **TTM:** janela móvel de doze meses, montada a partir dos dados trimestrais
  disponíveis.
- **Exercício fechado:** histórico anual baseado nos DFPs.

O conjunto de indicadores cobre, entre outros:

- rentabilidade e retorno: ROE, ROA, ROIC e margens;
- estrutura de capital: dívida líquida, alavancagem, liquidez e cobertura;
- crescimento: receita, lucro, EBITDA, EBIT e CAGRs;
- dados por ação: EPS, valor patrimonial e distribuições;
- avaliação: P/L, P/VP, PSR, EV/EBIT, EV/EBITDA, preço/FCF e dividend yield;
- métricas específicas de bancos e seguradoras, quando o regime contábil
  fornece os insumos aplicáveis.

Cada análise conserva a data de referência, o regime contábil, a origem dos
insumos, a base de preço/ações e o contrato da fórmula. Indicadores inaplicáveis
ou sem cobertura suficiente permanecem nulos com a razão correspondente.

## API

Com MongoDB e PostgreSQL disponíveis e o schema criado:

```bash
uv run uvicorn smaug.entrypoints.api:app --reload --host 0.0.0.0 --port 8000
```

A documentação interativa fica em <http://localhost:8000/docs>.

| Método | Endpoint | Função |
|---|---|---|
| `GET` | `/analysis` | Última análise persistida de cada ticker. |
| `GET` | `/analysis/{ticker}` | Visão TTM e histórico anual de um ticker. |
| `GET` | `/portfolio` | Lista de tickers favoritados. |
| `POST` | `/portfolio/{ticker}` | Favorita um ticker de forma idempotente. |
| `DELETE` | `/portfolio/{ticker}` | Remove um ticker dos favoritos. |

Os endpoints de análise são de leitura. Favoritos são preferências do usuário,
não resultados calculados, e por isso são a única escrita exposta pela API.

## Front-end

O front-end fica em `frontend/` e é uma aplicação separada do backend Python.
Ele busca dados da API no servidor; o navegador não acessa diretamente a API
para leituras. A exceção é o toggle de favoritos, que passa por uma rota
same-origin do Next.js.

Em um segundo terminal:

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

O front-end fica em <http://localhost:3000>. O `.env.local` usa:

```dotenv
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

Rotas principais:

- `/`: página inicial e busca de ticker;
- `/portfolio`: visão geral dos favoritos por setor;
- `/ticker/{symbol}`: detalhes do ticker, indicadores, histórico e gráficos.

Verificações do front-end:

```bash
npm run typecheck
npm run build
```

## Testes e qualidade

Na raiz do projeto:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Para mudanças no front-end:

```bash
cd frontend
npm run typecheck
npm run build
```

Quando o cache padrão do uv não estiver disponível no ambiente local, use um
cache temporário:

```bash
UV_CACHE_DIR=/tmp/project-smaug-uv-cache uv run pytest
```

## Estrutura do repositório

```text
src/smaug/
├── ingestion/     # fontes CVM/B3 e espelho cru em MongoDB
├── analysis/      # domínio, cálculo e persistência dos indicadores
├── portfolio/     # favoritos, identidade, classes e taxonomia
├── shared/        # configuração, conexões, artefatos e eventos
└── entrypoints/   # CLI e API FastAPI

frontend/          # aplicação Next.js
alembic/           # migrações do PostgreSQL
docs/adr/          # decisões de arquitetura e modelagem
tests/             # testes unitários e de integração isolada
```

## Documentação e fonte de verdade

- [`docs/ROADMAP.md`](docs/ROADMAP.md): objetivo e milestones M0–M3;
- [`docs/adr/`](docs/adr/): decisões imutáveis de arquitetura, modelagem e
  proveniência;
- [`AGENTS.md`](AGENTS.md): regras de engenharia e limites do projeto;
- [`docs/AGENTS.md`](docs/AGENTS.md): onde cada tipo de decisão deve ser
  documentado;
- issues do GitHub: trabalho pendente e próximos passos;
- `smaug doctor` e os testes: estado atual de cobertura e correção.

O README explica o funcionamento e o uso do sistema. Ele não substitui os
relatórios gerados pelos comandos nem deve ser tratado como um snapshot dos
dados atualmente persistidos.

## Status

- ✅ Ingestão CVM/B3 com espelho cru append-only em MongoDB.
- ✅ Ingestão sem `pycvm`, com leitura direta dos CSVs da CVM.
- ✅ Cálculo e persistência dos indicadores TTM e de exercícios fechados em
  PostgreSQL.
- ✅ API FastAPI para análises e gerenciamento de favoritos.
- ✅ Front-end Next.js para busca, carteira, indicadores e históricos.
- ⏳ Análise qualitativa por critérios/IA, prevista no milestone M3.
