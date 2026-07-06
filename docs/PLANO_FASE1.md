# Plano de Implementação — Fase 1 (Ingestão: CVM + brapi) · project-smaug

> Documento de plano fechado na sessão de planejamento. É a fonte de verdade do
> **como** da Fase 1. Os critérios do **o quê / porquê** vivem em
> `docs/preview_fase1_criterios_implementacao.md`.
>
> ⚠️ A **seção 0** abaixo registra o que mudou entre o plano e a implementação
> (o pivô de brapi para CVM). As seções 1–11 preservam o plano original.

---

## 0. Atualização pós-implementação (o que mudou vs. o plano)

O plano original previa **brapi como fonte única**. Na prática, o **plano gratuito
da brapi não cobre a carteira**: só PETR4/VALE3 retornam; bancos e seguradoras dão
`403` (restrição de plano). Decisão: **CVM (dados.cvm.gov.br) como fonte primária**,
com o **brapi mantido como alternativa alternável por config** — para não reescrever
código no dia em que o plano pago for assinado.

**O "seam" (troca sem reescrita):** ambas as fontes implementam a mesma porta
`RawDataSource` (`ingestion/domain/ports.py`). A fonte ativa é escolhida por
`INGESTION_SOURCE` (`cvm` padrão | `brapi`) no `.env`. `RawIngestion.source` marca
cada registro, então as duas convivem no mesmo `raw_ingestions`.

**CVM — como funciona:** baixa o ZIP anual do ITR (`CVM_YEAR`, default 2024), cacheia
em `.cache/` (gitignored), **esvazia os membros DMPL** (o parser da `pycvm` quebra
neles), parseia com `pycvm` e espelha as contas cruas de **BPA/BPP/DRE/DFC** (código,
nome, valor `Decimal`), **sem cálculo**. Cobre a carteira inteira, de graça, sem token.
Bancos e seguradoras vêm no formato regulado deles.

**Módulos por fonte:**
- **brapi:** `balanceSheetHistoryQuarterly`, `incomeStatementHistoryQuarterly`,
  `cashflowHistoryQuarterly`, `defaultKeyStatistics`, `financialData`, `dividends`.
- **CVM:** `BPA`, `BPP`, `DRE`, `DFC`.

**Mapa ticker → código CVM** (novo dado de referência em
`portfolio/domain/cvm_codes.py`), verificado contra os nomes reais no ITR 2024:

| Ticker | CVM | Empresa |
|---|---|---|
| PETR4 | 9512 | Petrobras |
| VALE3 | 4170 | Vale |
| SAPR11 | 18627 | Sanepar |
| TAEE11 | 20257 | Taesa |
| WEGE3 | 5410 | WEG |
| BBAS3 | 1023 | Banco do Brasil |
| BBDC4 | 906 | Bradesco |
| BBSE3 | 23159 | BB Seguridade |
| CXSE3 | 23795 | Caixa Seguridade |

**Relatório ciente da fonte:** um `ReportProfile` por fonte. brapi conta trimestres e
checa campos; CVM conta **contas** e checa **âncoras setoriais** por código/nome
(âncoras validadas no ITR 2024; ver seção 6).

**Descobertas da Fase 1:**
- **brapi grátis não cobre a carteira** — só PETR4/VALE3; o resto dá `403`
  (plan-restricted, tratado como *skip*, não erro).
- **CXSE3 (Caixa Seguridade) declara como holding**, não no formato seguradora: a
  DRE 3.01 é "Receita de Venda", não "Atividades Seguradoras". O relatório setorial
  sinaliza "Receita de seguros" **ausente** — descoberta, não bug.
- **Patrimônio Líquido tem código diferente por setor** na CVM (banco `2.07`,
  demais `2.03`) → o relatório casa PL por **nome**, receita por **código** (`3.01`).
- **`pycvm` não é tipada** (override de mypy p/ `cvm.*`) e seu parser de DMPL quebra
  no ITR 2024 real (`KeyError: 'Patrimônio Líquido'`) — contornado esvaziando o DMPL.

**Segurança:** corrigido um vazamento — o `httpx` logava a URL de requisição com
`?token=`; o logger do `httpx` foi silenciado abaixo de `WARNING`.

---

## 1. Objetivo da fase

Consumir e persistir os dados fundamentais das 9 ações da carteira a partir da
API **brapi**, de forma fiel e auditável, e validar se a fundação de dados
sustenta a Fase 2 (análise por critérios).

**Fora de escopo (não vazar Fase 2 para dentro da Fase 1):** cálculo de
indicadores, aplicação de critérios, qualquer LLM/RAG/agente, leitura de RIs em
PDF, decisão de compra/venda.

**Carteira-alvo:** PETR4, VALE3, SAPR11, TAEE11, WEGE3 (não-financeiras);
BBAS3, BBDC4 (bancos); BBSE3, CXSE3 (seguradoras).

---

## 2. Decisões travadas (esta sessão)

| Tema | Decisão | Motivo |
|---|---|---|
| Linguagem | Python **3.13** (fixado via uv; sistema tem 3.14) | Ecossistema mais maduro p/ Beanie/motor |
| Gerenciador | **uv** (deps + tasks + versão de Python) | Moderno, rápido, `pyproject.toml` padrão |
| Web framework | **FastAPI** como esqueleto do sistema | Habita a Fase 2; **não** é o gatilho da Fase 1 |
| Gatilho da Fase 1 | **CLI** (`python -m smaug.ingest`) | Coletor em lote; endpoint HTTP fica p/ depois |
| Banco (Fase 1) | **MongoDB via Docker** + **Beanie** (ODM tipado) | Espelho documental fiel; casa com "tudo tipado" |
| Banco (Fase 2) | **PostgreSQL 17 + SQLAlchemy + Alembic** — deferido | Guardará os dados **derivados/calculados** |
| Acesso a dados | Async (motor via Beanie) | Consistente com FastAPI e com a Fase 2 |
| Comunicação entre contextos | **EventBus in-process** já na Fase 1 | Trava o padrão de 0 acoplamento p/ a Fase 2 plugar |
| Qualidade | **mypy strict** + **ruff** + **pytest** | Tudo tipado; padrão herdado do projeto anterior |
| Repositório GitHub | **público**, nome `project-smaug` | Escolha do usuário |
| Multi-tenant | **Removido** (uso pessoal, sem `company_id`) | Não é SaaS |

---

## 3. Arquitetura (DDD Lite, contextos isolados)

### 3.1 Camadas (hierarquia de import)

```
domain/          ← lógica pura, entidades frozen, interfaces de repositório, eventos
    ↓
application/     ← casos de uso, orquestração, publicação de eventos
    ↓
infrastructure/  ← modelos Beanie, repositórios, cliente brapi
    ↓
entrypoints/     ← CLI (Fase 1) e, futuramente, rotas FastAPI
```

**Regras absolutas:**
- `domain/` nunca importa `infrastructure/` (sem Beanie/motor/httpx no domínio).
- `application/` só toca infra através de **interfaces** definidas no domínio.
- Entrypoints (CLI/API) não contêm lógica de negócio — chamam casos de uso.
- Modelo de infra (documento Beanie) **não vaza**: `_to_entity()` / `_to_document()`
  ficam dentro do repositório.

### 3.2 Bounded contexts da Fase 1

| Módulo | Responsabilidade |
|---|---|
| `ingestion` | Buscar na brapi + persistir o espelho cru + publicar evento |
| `portfolio` | Mapa determinístico **ticker → setor** (dado de referência fixo) |
| `shared` | Config, conexão Mongo, **EventBus**, erros, logging |

### 3.3 Comunicação por eventos

`ingestion` publica um evento de domínio (ex.: `RawIngestionStored`) no
`EventBus` de `shared`. Na Fase 1 **não há assinante** — o barramento existe para
a Fase 2 (análise) se inscrever sem que a ingestão saiba que ela existe.
Contrato do evento definido agora; implementação do bus é síncrona e in-process.

---

## 4. Modelo de persistência — o "espelho fiel"

### 4.1 Coleção `raw_ingestions` (append-only)

Um **documento por chamada de API** (fotografa a página inteira, não recorta a
notícia). Campos:

| Campo | Tipo | Descrição |
|---|---|---|
| `_id` | ObjectId/UUID | Identidade do registro |
| `ticker` | str | Ex.: `PETR4` |
| `source` | str | Ex.: `brapi` |
| `module` | str | Ex.: `incomeStatementHistoryQuarterly` |
| `fetched_at` | datetime (UTC) | Quando o dado foi puxado |
| `request` | doc | Endpoint + params usados (auditoria) |
| `http_status` | int | Código de resposta |
| `payload` | doc (JSON cru) | **Resposta completa e não interpretada** do módulo |

- **Chave lógica de append:** `ticker + module + fetched_at`.
- **Nunca sobrescreve.** Re-coletar insere um novo documento → preserva o
  histórico de revisões (o brapi pode republicar um balanço).
- **Período de referência não é coluna forçada** (uma chamada abrange vários
  trimestres). A cobertura de períodos é **derivada do payload** pelo relatório
  de completude.
- Nunca guardar dado normalizado/calculado — isso é Fase 2.

---

## 5. Fluxo da Fase 1 (uma responsabilidade por função)

```
buscar (brapi client)  →  salvar (repositório → Mongo)  →  publicar evento
                                                        →  relatar (completude)
```

1. **Buscar** — cliente brapi autenticado (token obrigatório), puxa os módulos
   de histórico trimestral + estatísticas-chave + dividendos, com a **máxima
   profundidade** que o plano gratuito permitir.
2. **Salvar** — repositório grava o documento cru (append).
3. **Publicar** — `EventBus` emite `RawIngestionStored`.
4. **Relatar** — comando separado gera o relatório de completude.

### 5.1 Resiliência mínima (sem overengineering)

- Tratar por código documentado: **401** (token), **402/429** (limite),
  **404** (ticker inexistente) — cada um com ação distinta (parar/esperar/pular).
- **Um ticker que falha não derruba os outros** — isolamento por consulta.
- **Pausa entre requisições** para respeitar rate limit. Sem retry elaborado.
- **Log de coleta:** tickers consultados, sucesso/falha, código, nº de trimestres.

---

## 6. Relatório de completude (o entregável que valida a fundação)

Comando separado (`python -m smaug.report`) que lê o espelho e responde, **por
ticker**:

- Quais campos vieram preenchidos vs vazios/nulos.
- Quantos trimestres de histórico vieram.
- **Verificação setorial dirigida** (ponto de maior risco):
  - **Bancos (BBAS3, BBDC4):** patrimônio no formato de instituição financeira,
    base para ROE, dados de rentabilidade.
  - **Seguradoras (BBSE3, CXSE3):** base para ROE e resultado do negócio.
  - **Não-financeiras:** receita, lucro, EBITDA, dívida líquida, margens.
- Data da coleta e erros por ticker.

> Campo setorial crítico faltando = **descoberta da Fase 1, não bug**. Decide se
> o brapi basta ou se precisa de fonte complementar naquele setor.

---

## 7. Segredos, reprodutibilidade e segurança

- **Repo é PÚBLICO** → cuidado redobrado. Token brapi **só** em `.env`
  (gitignored). Apenas `.env.example` é versionado. Nunca hardcoded, nunca
  commitado.
- Dependências fixadas (`uv.lock`) — rodar amanhã reproduz o comportamento.
- Coleta re-executável com segurança (append preserva histórico).

---

## 8. Testes

- TDD: red → green → refactor. Nomes `should_X_when_Y`.
- **Nunca bater na API real nos testes** — usar respostas gravadas (fixtures).
- mypy strict e ruff são gate rígido. Alvo de cobertura a definir na
  implementação (foco em cobrir o núcleo: client, repositório, casos de uso).

---

## 9. Definição de "pronto" para a Fase 1

- [x] 9 ações coletadas (via CVM, sem token), com tratamento de falha por ticker.
- [x] Dado cru persistido com ticker, data de consulta, fonte e módulo.
- [x] Re-rodar a coleta é seguro e não corrompe o histórico (append-only).
- [x] Relatório de completude por ticker, incl. verificação setorial (por fonte).
- [x] Confirmado por inspeção real que os dados setoriais estão presentes — e a
      única lacuna (CXSE3 declara como holding) está documentada na seção 0.
- [x] Token fora do código e fora dos logs; dependências fixadas; log de coleta.

---

## 10. Estrutura de pastas (alvo)

```
project-smaug/
├── docs/
│   ├── PLANO_FASE1.md
│   └── preview_fase1_criterios_implementacao.md
├── src/smaug/
│   ├── ingestion/
│   │   ├── domain/          (entities.py, repositories.py, events.py)
│   │   ├── application/      (use cases)
│   │   └── infrastructure/   (brapi client, beanie models, repositories)
│   ├── portfolio/
│   │   └── domain/           (ticker → setor)
│   ├── shared/               (config, db, events, logging, errors)
│   └── entrypoints/          (cli.py)
├── tests/
├── docker-compose.yml        (MongoDB)
├── pyproject.toml            (uv, ruff, mypy strict, pytest)
├── .env.example
├── .gitignore
└── README.md
```

---

## 11. Roadmap de implementação (pós-scaffold — próximas sessões)

1. `shared`: config (Pydantic Settings) + conexão Mongo (Beanie init) + EventBus.
2. `portfolio`: mapa ticker → setor.
3. `ingestion/infrastructure`: cliente brapi (httpx async) + resiliência.
4. `ingestion`: modelo Beanie + repositório (append) + entidade de domínio.
5. `ingestion/application`: caso de uso de ingestão + publicação de evento.
6. `entrypoints`: CLI de coleta.
7. Relatório de completude + verificação setorial.
8. Testes em cada passo.

> Esta sessão entrega apenas o **plano + esqueleto pronto para o initial commit**.
> Nenhuma lógica de coleta é implementada ainda.
