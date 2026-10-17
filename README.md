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

<!-- BEGIN SMAUG SETUP GUIDE -->

## Instalação e execução com Docker

Modalidade deste guia: **full**. `backend` inclui MongoDB,
PostgreSQL, ingestão CVM/B3, análise, fórmulas, doctor e API; `full` acrescenta
o frontend. É possível alternar entre as modalidades mantendo os dados.

Pré-requisito: Docker com Compose v2 instalado, iniciado e acessível pelo seu
usuário, executando containers Linux. Confira com `docker info` e
`docker compose version`. Para usar os instaladores, não é necessário instalar
Git, Python, uv, Node ou Go no host. A configuração do conector MCP a partir
do código-fonte, descrita adiante, tem seus próprios pré-requisitos.

Baixe o pacote da modalidade desejada e seu arquivo `.sha256` em
[Releases](https://github.com/gzucob/project-smaug/releases). Substitua
`VERSAO` nos comandos pelo identificador da release escolhida. Downloads e a
coleta posterior de CVM/B3 precisam de internet. A instalação prepara bancos
vazios, sem fazer ingestão ou análise automaticamente.

### Linux e WSL

Baixe `smaug-full-VERSAO.tar.gz` e
`smaug-full-VERSAO.tar.gz.sha256`. Na pasta dos downloads:

```bash
sha256sum --check smaug-full-VERSAO.tar.gz.sha256
mkdir -p smaug-installer
tar -xzf smaug-full-VERSAO.tar.gz -C smaug-installer
bash smaug-installer/install.sh --edition full
smaugctl() { "${XDG_CONFIG_HOME:-$HOME/.config}/smaug/smaugctl" "$@"; }
```

A instalação padrão fica em `~/.config/smaug`, ou em `$XDG_CONFIG_HOME/smaug`
quando configurado. No WSL, o Docker precisa estar acessível dentro da
distribuição Linux. A função acima permite usar os próximos comandos neste
terminal; o instalador também informa o caminho completo do gerenciador.

### macOS

Baixe `smaug-full-VERSAO.tar.gz` e
`smaug-full-VERSAO.tar.gz.sha256`. Na pasta dos downloads:

```bash
shasum -a 256 --check smaug-full-VERSAO.tar.gz.sha256
mkdir -p smaug-installer
tar -xzf smaug-full-VERSAO.tar.gz -C smaug-installer
bash smaug-installer/install.sh --edition full
smaugctl() { "$HOME/Library/Application Support/smaug/smaugctl" "$@"; }
```

A instalação padrão fica em `~/Library/Application Support/smaug`. A função
acima permite usar os próximos comandos neste terminal. O macOS virtual usado
para validação precisa conseguir executar o engine de containers Linux.

### Windows

Baixe `smaug-full-VERSAO.zip` e
`smaug-full-VERSAO.zip.sha256`. Use Docker Desktop em modo de containers
Linux. Na pasta dos downloads, execute no PowerShell:

```powershell
$smaugArchive = 'smaug-full-VERSAO.zip'
$smaugExpected = ((Get-Content -LiteralPath "$smaugArchive.sha256") -split '\s+')[0]
$smaugActual = (Get-FileHash -Algorithm SHA256 -LiteralPath $smaugArchive).Hash
if ($smaugActual -ne $smaugExpected) { throw 'Checksum diferente do publicado' }
Expand-Archive -LiteralPath $smaugArchive -DestinationPath '.\smaug-installer'
& .\smaug-installer\install.ps1 --edition full
function smaugctl { & (Join-Path $env:APPDATA 'smaug\smaugctl.ps1') @args }
```

A instalação padrão fica em `%APPDATA%\smaug`. A função acima permite usar
os próximos comandos neste terminal. Os scripts verificam também os arquivos
extraídos antes de executar o gerenciador. A execução do script deve seguir a
política PowerShell do seu ambiente; o instalador não altera essa política.

### Início, diagnóstico e configuração

```text
smaugctl status
smaugctl start
smaugctl check
smaugctl cli formulas
smaugctl cli formulas pe_basic --json
smaugctl logs api
smaugctl stop
```

A API fica em <http://127.0.0.1:8000/docs>; na modalidade `full`, o frontend
fica em <http://127.0.0.1:3000>. Só a própria máquina acessa essas portas.
Os bancos ficam na rede interna do Compose. Cada instalação tem volumes
próprios e não reutiliza os bancos do checkout de desenvolvimento.

`smaugctl check` verifica a operação. Uma instalação nova deve informar
`ready: true` e `data: empty`. Isso significa que os serviços estão prontos,
mas os dados ainda não foram preparados. `smaug doctor` verifica a cobertura
dos dados de análise; ele não substitui o diagnóstico da instalação.

`GET /health` identifica o processo e seu commit. `GET /ready` verifica os
bancos e a revisão Alembic e informa se há análises. `GET /formulas` e
`GET /formulas/{indicator}` expõem a referência das fórmulas da versão.

Se já houver serviços usando as portas padrão, personalize a instalação.
No Linux/macOS, por exemplo:

```bash
bash smaug-installer/install.sh --dir "$HOME/smaug-teste" --api-port 18000 --web-port 13000
```

No PowerShell:

```powershell
& .\smaug-installer\install.ps1 --dir "$env:USERPROFILE\smaug-teste" --api-port 18000 --web-port 13000
```

Ao escolher outro diretório, ajuste a função `smaugctl` para chamar o
`smaugctl` ou `smaugctl.ps1` desse diretório. Nesse exemplo, abra o frontend
em <http://127.0.0.1:13000> e a API em <http://127.0.0.1:18000/docs>.

Reexecutar o instalador preserva configuração e dados. Portas e atualização
de preços ficam em `config.json`; edite-o com os serviços parados e preserve
o identificador `project`. Fórmulas podem ser consultadas pela CLI mesmo com
os bancos parados. A API e o frontend precisam dos serviços em execução.

### Primeiros dados e análise

Escolha os tickers e os anos de documentos deliberadamente. Um exemplo com
PETR4 e arquivos explicitamente selecionados é:

```text
smaugctl cli ingest --ticker PETR4 --document DFP --year 2025
smaugctl cli ingest --ticker PETR4 --document ITR --from-year 2025 --to-year 2026
smaugctl cli analyze --ticker PETR4
smaugctl cli doctor --ticker PETR4
```

A ingestão baixa fontes públicas e persiste o espelho; `analyze` calcula e
persiste os indicadores; `doctor` lê os resultados e suas causas de nulos.
A coleta pode demorar e a disponibilidade das fontes determina a cobertura.
Os anos acima são exemplos, não uma seleção automática do período atual.
TTM precisa de demonstrações anuais e trimestrais compatíveis; lacunas de
cobertura devem ser examinadas no doctor. Nulos classificados não são zeros.
O doctor retorna código diferente de zero quando há nulos não classificados.

### Atualização, modalidades, backup e recuperação

```text
smaugctl update
smaugctl edition backend
smaugctl edition full
smaugctl backup
smaugctl restore --backup CAMINHO_DO_BACKUP
```

Após cada squash merge na `main`, o CI valida o projeto e a publicação
valida backend/full e o conector MCP em amd64/arm64 antes de publicar a release.
A versão identifica a data UTC e o commit; o manifesto registra SHA completo,
revisão Alembic, digests das imagens e checksums. Uma publicação parcial não
substitui a última release completa.

`update` consulta a última release completa, verifica o pacote e baixa as
imagens antes de parar serviços. Também aceita `--source DIRETORIO_EXTRAIDO`
para um pacote já baixado. Trocar de modalidade preserva os dados. O software
é atualizado por comando explícito; atualizar não faz ingestão ou recálculo
geral. A rotina existente de atualização de preços continua independente.

Antes de migrar, o gerenciador para os escritores e cria um backup do
PostgreSQL. Falha no backup impede a migração. CLI ativa e atualizações
simultâneas são recusadas. O arquivo `pending.json` registra uma operação
interrompida e bloqueia o início normal até a recuperação.

Se uma atualização falhar, examine `smaugctl status` e `smaugctl logs api`.
Use `smaugctl update` para repetir a operação pendente ou selecione um backup
com `restore --backup`. A restauração substitui o estado dos bancos dessa
instalação; não há retorno automático de software antigo contra um schema
possivelmente incompatível. Isso vale também para repetir uma instalação
inicial interrompida usando o gerenciador no diretório escolhido.

`backup` copia PostgreSQL, MongoDB, configuração e artefatos Bronze. Caches
ficam fora. O backup automático pré-migração contém PostgreSQL e configuração.
Backups ficam em `backups` e não são removidos automaticamente. A restauração
preserva artefatos Bronze posteriores que não estão no arquivo selecionado.
Mantenha cópias dos backups em outro armazenamento para proteger a instalação.

Após uma interrupção abrupta pode restar `operation.lock`. Confira se nenhum
gerenciador ou container CLI continua ativo antes de remover esse diretório.
Parar, atualizar ou reinstalar não apaga volumes. O gerenciador não oferece
exclusão de dados; mudanças nas imagens dos bancos exigem upgrade próprio.

### Instalar e conectar o MCP

Modalidade do pacote separado: **mcp**.

O conector roda por `stdio` usando a imagem Python já distribuída pelo Smaug.
É necessário ter Docker instalado e em execução, com containers Linux. Para
os instaladores MCP, Git, Python, uv, Node e Go não precisam estar no host.
A API deve estar acessível para consultar dados; o guia funciona com ela parada.

**Instalação backend/full:** o conector já está incluído. Em instalações
anteriores, use `smaugctl update` para obter a versão que fornece o comando.
Registre no cliente desejado:

```text
smaugctl mcp register --client codex
smaugctl mcp register --client claude
smaugctl mcp register --client both
```

Também é possível registrar ao instalar backend/full com
`install.sh --mcp-client codex` ou `install.ps1 --mcp-client both`.
O cliente inicia `smaugctl mcp run`; esse comando não inicia API, bancos,
frontend ou migrações. Ele não usa o bloqueio de escrita das operações CLI e
permite conexões simultâneas. A comunicação usa `http://api:8000` na rede
interna da instalação, independentemente da porta publicada no host.

**Pacote separado — modalidade `mcp`:** baixe `smaug-mcp-VERSAO.tar.gz` ou
`smaug-mcp-VERSAO.zip` e o checksum correspondente em
[Releases](https://github.com/gzucob/project-smaug/releases). O pacote instala
somente o conector para uma API existente, em diretório próprio `smaug-mcp`,
e baixa apenas a imagem de backend. Não cria bancos, API, frontend ou volumes.
Substitua `VERSAO` e a URL abaixo pelos valores desejados.

No **Linux/WSL**, na pasta dos downloads:

```bash
sha256sum --check smaug-mcp-VERSAO.tar.gz.sha256
mkdir -p smaug-mcp-installer
tar -xzf smaug-mcp-VERSAO.tar.gz -C smaug-mcp-installer
bash smaug-mcp-installer/install.sh --api-base-url http://127.0.0.1:8000 --client codex
smaugmcp() { "${XDG_CONFIG_HOME:-$HOME/.config}/smaug-mcp/smaugctl" "$@"; }
```

No **macOS**:

```bash
shasum -a 256 --check smaug-mcp-VERSAO.tar.gz.sha256
mkdir -p smaug-mcp-installer
tar -xzf smaug-mcp-VERSAO.tar.gz -C smaug-mcp-installer
bash smaug-mcp-installer/install.sh --api-base-url http://127.0.0.1:8000 --client both
smaugmcp() { "$HOME/Library/Application Support/smaug-mcp/smaugctl" "$@"; }
```

No **Windows**, execute no PowerShell:

```powershell
$smaugArchive = 'smaug-mcp-VERSAO.zip'
$smaugExpected = ((Get-Content -LiteralPath "$smaugArchive.sha256") -split '\s+')[0]
$smaugActual = (Get-FileHash -Algorithm SHA256 -LiteralPath $smaugArchive).Hash
if ($smaugActual -ne $smaugExpected) { throw 'Checksum diferente do publicado' }
Expand-Archive -LiteralPath $smaugArchive -DestinationPath '.\smaug-mcp-installer'
& .\smaug-mcp-installer\install.ps1 --api-base-url http://127.0.0.1:8000 --client both
function smaugmcp { & (Join-Path $env:APPDATA 'smaug-mcp\smaugctl.ps1') @args }
```

A opção `--client` é opcional; sem ela, só o conector é instalado. Para trocar
a URL, repita o instalador com `--api-base-url URL`; sem essa opção, a URL
salva é preservada. `--dir DIRETORIO` deve vir antes das demais opções do
instalador e permite escolher outra instalação. Ajuste a função `smaugmcp`
se personalizar o diretório. Para uma API local publicada em outra porta,
como `18000`, use essa porta na URL da API, nunca a porta do frontend.

O conector separado usa a rede do host no Docker Engine Linux; no Docker
Desktop para Linux/WSL, essa opção precisa estar habilitada para alcançar a
API local. Em Windows/macOS, endereços de loopback (`127.0.0.1`, `localhost`
e `::1`) são traduzidos para `host.docker.internal` dentro do container.
URLs remotas mantêm seu endereço. Use um Docker local: um contexto Docker
remoto muda qual máquina o container enxerga como host.

O pacote separado oferece:

```text
smaugmcp mcp status
smaugmcp mcp register --client codex
smaugmcp mcp register --client claude
smaugmcp mcp config --client codex
smaugmcp mcp config --client claude
smaugmcp mcp update
smaugmcp mcp update --source DIRETORIO_EXTRAIDO_DO_PACOTE_MCP
```

A atualização baixa o último pacote MCP publicado, verifica os checksums,
prepara a imagem e muda a versão para as próximas conexões. Não atualiza a
API externa. Reinicie as conexões MCP após atualizar. As três distribuições
são publicadas juntas, após o CI da `main` e os testes de instalação.

**Configuração dos clientes:** o registro do Codex requer o comando `codex`
instalado e usa `codex mcp add`, respeitando `CODEX_HOME`. Sem a CLI, copie a
saída de `mcp config --client codex` para seu `config.toml`. O Claude Desktop
usa `claude_desktop_config.json` no diretório de configuração do usuário em
Windows/macOS. Use `--config CAMINHO_JSON` para um arquivo Claude específico;
no Linux, é necessário fornecer esse caminho explicitamente.

O registro preserva as outras conexões e configurações. Se o nome `smaug` já
apontar para outro comando, escolha `--name smaug-remoto` ou confira a saída
de `mcp config` e use `mcp register --client CLIENTE --replace`. Antes de uma
alteração, é criado um backup do arquivo do cliente. Repetir um registro
idêntico não altera o arquivo. O caminho absoluto do Docker é registrado
para que o cliente não dependa do PATH de um terminal. O registro de `both`
é sequencial; se o segundo cliente falhar, o primeiro pode já estar pronto.

Registre no mesmo sistema em que o cliente roda: para Codex no WSL, execute
no WSL; para Claude Desktop no Windows, use o pacote e o PowerShell do Windows.
Copiar um caminho Linux para o JSON de um aplicativo Windows não o torna
executável. Reinicie o cliente após registrar e peça o guia ou o diagnóstico.

A implementação segue os formatos documentados de
[registro MCP no Codex](https://developers.openai.com/codex/mcp/) e
[MCP local no Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers).
A rede do pacote separado segue as opções de
[rede do host](https://docs.docker.com/engine/network/drivers/host/) e
[Docker Desktop](https://docs.docker.com/desktop/features/networking/).

Depois de conectado, `get_setup_guide` e o recurso `smaug://setup` funcionam
mesmo com a API desligada. `get_service_status` consulta apenas `/health` e
`/ready`: verifica a API e as dependências relatadas por ela. Não inspeciona
Docker ou o frontend e não inicia serviços. Nenhuma dessas leituras instala,
calcula indicadores ou executa manutenção.

O desenvolvimento a partir do checkout continua disponível com Python 3.13
e uv: rode `uv sync` e configure o cliente para iniciar
`uv --directory /CAMINHO/ABSOLUTO/project-smaug run smaug-mcp`. Nessa forma,
`MCP_API_BASE_URL` seleciona a API e `MCP_API_TIMEOUT_SECONDS` define o timeout.
Veja os exemplos de clientes na seção MCP do README. O guia está disponível
no README e como `SETUP.md` nos três pacotes antes de conectar o próprio MCP.

<!-- END SMAUG SETUP GUIDE -->

### Publicação e desenvolvimento do instalador

O `smaugctl` é escrito em Go 1.27.2, sem dependências externas; Go é necessário
apenas para desenvolver e compilar o gerenciador. O backend continua Python
3.13 e o frontend continua Next.js/React. O CI roda os testes do gerenciador
em Windows, macOS e Linux, além dos testes Compose completos em Linux amd64
e arm64. A experiência Docker Desktop em Windows/macOS ainda precisa de
validação manual em Windows e macOS virtual com engine de containers Linux antes de anunciar esses sistemas como
validados de ponta a ponta, acompanhada na
[issue #338](https://github.com/gzucob/project-smaug/issues/338).

Na primeira publicação, os dois pacotes GHCR `project-smaug-backend` e
`project-smaug-frontend` precisam estar públicos. O GitHub cria novos pacotes
como privados: ajuste a visibilidade nas configurações de cada pacote e
reexecute o workflow de release caso a verificação anônima tenha falhado.
Esse é um ajuste inicial da plataforma; as publicações seguintes são
automáticas. A release não é disponibilizada enquanto suas imagens exigirem
login. [Visibilidade de pacotes no GitHub](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).
O ajuste inicial é acompanhado na
[issue #339](https://github.com/gzucob/project-smaug/issues/339).

```bash
cd installer
go test ./...
go vet ./...
cd ..
# Requer Go, uv e Docker; cria apenas ambientes descartáveis de teste.
GITHUB_SHA=$(git rev-parse HEAD) bash scripts/installer/ci-smoke.sh
```

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
- `MONGO_URI` e `MONGO_DB`: conexão do espelho cru. Se outro projeto usar a
  porta `27017`, defina `MONGO_PORT=27018` e
  `MONGO_URI=mongodb://localhost:27018` no `.env` antes de subir o Docker.
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

Cada recalculação substitui atomicamente a análise anterior: fica um TTM atual
por ticker e uma análise por exercício fechado. O histórico anual continua
disponível; versões anteriores do cálculo não são retidas. A migração `0044`
remove essas versões de bancos existentes, mantendo os resultados selecionados
pelas leituras atuais. Os dados de origem no MongoDB continuam preservados.

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
manutenção. `prune` permanece como compatibilidade para bancos anteriores à
migração de retenção; nas gravações atuais, recalculações já substituem o
resultado anterior.

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

Ao iniciar a API, um processo CLI atualiza as cotações em segundo plano para
os ativos que já possuem análises. A verificação se repete a cada seis horas
enquanto a API estiver aberta; uma conclusão recente evita trabalho duplicado
após reiniciar. Não exige VPS, cron nem serviço externo. Execute
`uv run alembic upgrade head` antes de iniciar a versão atualizada.

Se o projeto ficar semanas ou meses desligado, a próxima verificação consulta
os arquivos anuais COTAHIST da B3 e recupera os pregões do intervalo, inclusive
quando atravessa a virada do ano. Históricos são reconstruídos na base de ações
resolvida pelo espelho CVM existente; preços alterados também provocam a
recomputação dos indicadores dependentes. Essa rotina não atualiza os
fundamentos CVM: a ingestão continua sendo uma etapa própria.

Os dados salvos continuam acessíveis durante a atualização. Falhas são
registradas sem marcar a execução como concluída; o próximo ciclo tenta de novo.
Atualizações de cada ativo são retomadas por comparação com o que foi salvo,
sem apagar snapshots anteriores. Uma primeira instalação ainda precisa executar
a ingestão e `smaug analyze` para preparar seus ativos.

A atualização é silenciosa: a home exibe os dados persistidos sem painel de
progresso, lista de falhas ou alertas por ticker. Enquanto a aplicação está
aberta, as páginas consultam os dados salvos em segundo plano a cada minuto;
ao voltar para a aba, consultam novamente. Essa leitura não dispara downloads
nem cálculos. A data do último negócio aparece discretamente junto à cotação
na página do ativo. Falhas ficam nos logs e no diagnóstico operacional da API.
Para desativar o worker, defina `AUTO_PRICE_REFRESH=false`; para alterar o
intervalo de verificação da B3, use `PRICE_REFRESH_INTERVAL_SECONDS` (padrão
`21600`).
Também é possível executar uma verificação avulsa com `uv run smaug sync-prices`.
Use `--force` para verificar imediatamente, sem aguardar o intervalo.

A cotação pode vir de um ano anterior: a busca do último negócio cobre o período
suportado desde 2010. Sua data original é preservada. A interface distingue o
preço negociado da base ajustada por desdobramentos, grupamentos e bonificações
usada nos indicadores. Uma consulta sem novos negócios é uma verificação válida.

A rotina tenta atualizar os ativos já analisados, sem classificá-los por
liquidez, free float ou situação cadastral. Registra apenas as falhas por ativo,
preserva os dados salvos quando uma tentativa falha e volta a tentar no próximo
ciclo. Quando não há negócio recente, usa o último preço negociado disponível,
com sua data original. Se nenhum preço estiver disponível, a interface mostra
`n/d`, sem alerta.
Os indicadores continuam usando a base ajustada por eventos de capital e o
espelho CVM disponível; a rotina não faz ingestão dos fundamentos.



| Método | Endpoint | Função |
|---|---|---|
| `GET` | `/prices/refresh-status` | Estado e última conclusão da atualização automática. |
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

Para uso cotidiano local, execute em modo de produção:

```bash
cd frontend
npm run local
```

Esse comando compila e inicia o frontend em `0.0.0.0:3000`. A compilação usa
`.next-local/`, separada do cache de desenvolvimento. Depois da primeira
compilação, `npm run start:local` inicia novamente a versão compilada; após
alterações de código, rode `npm run local` outra vez. Encerre a instância anterior
antes de recompilar essa mesma versão. `npm run dev` continua disponível para
desenvolvimento; apenas um servidor pode ocupar a porta 3000.

Início e Favoritos usam uma projeção compacta dos indicadores persistidos.
Na tela do ticker, seções independentes carregam progressivamente. A atualização
silenciosa consulta um token de mudanças a cada minuto e ao retornar à aba;
ela só refaz a página quando os dados mudam e aguarda o fechamento de um modal
ou o término de uma edição antes de aplicar a atualização.

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

## MCP local

Para uso sem checkout, instale uma modalidade backend/full ou o pacote
separado MCP e use `smaugctl mcp register --client codex|claude|both`, conforme
o guia de instalação acima. Os exemplos abaixo são a alternativa para
desenvolver o conector a partir do código-fonte.

O servidor MCP permite consultar os dados do Smaug em clientes como Codex e
Claude Desktop. Ele usa `stdio`; as consultas de dados consomem a API existente,
que deve estar em execução com acesso aos bancos necessários. A configuração é manual.

Prepare as dependências com `uv sync`. Em um terminal, inicie a API:

```bash
uv run uvicorn smaug.entrypoints.api:app --host 127.0.0.1 --port 8000
```

O cliente MCP inicia o processo usando este comando:

```bash
uv --directory /caminho/para/project-smaug run smaug-mcp
```

Executado diretamente no terminal, o processo aguarda mensagens MCP em stdin.
Stdout contém apenas mensagens do protocolo; os logs usam stderr. A MCP pode
iniciar, listar ferramentas e fornecer o guia sem a API, mas as consultas de dados falham enquanto ela
estiver indisponível. Ela não inicia a API ou comandos de atualização. A API
continua seguindo sua própria configuração de atualização automática de preços.

Configuração do Codex, em `~/.codex/config.toml`:

```toml
[mcp_servers.smaug]
command = "uv"
args = ["--directory", "/caminho/para/project-smaug", "run", "smaug-mcp"]

[mcp_servers.smaug.env]
MCP_API_BASE_URL = "http://127.0.0.1:8000"
MCP_API_TIMEOUT_SECONDS = "30"
```

Configuração do Claude Desktop, no arquivo `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "smaug": {
      "command": "uv",
      "args": ["--directory", "/caminho/para/project-smaug", "run", "smaug-mcp"],
      "env": {
        "MCP_API_BASE_URL": "http://127.0.0.1:8000",
        "MCP_API_TIMEOUT_SECONDS": "30"
      }
    }
  }
}
```

Substitua o caminho pelo checkout local e preserve as outras configurações do
cliente. Se `uv` não estiver no PATH do cliente, use o caminho absoluto do
executável. Quando o cliente roda no Windows e o projeto no WSL, use
`command = "wsl.exe"` e coloque `"--", "/caminho/absoluto/do/uv"` antes dos
argumentos acima; use os caminhos Linux do projeto e do uv dentro do WSL.

| Ferramenta | Dados retornados |
|---|---|
| `get_setup_guide` | Guia offline por `platform` (all/linux/macos/windows) e `edition` (backend/full/mcp) |
| `get_service_status` | Estado da API e dependências informadas por `/health` e `/ready` |
| `list_portfolio` | Tickers favoritos |
| `list_analysis_summaries` | Resumos TTM persistidos |
| `get_ticker_analysis` | TTM completo e histórico anual, com contratos e proveniência |
| `get_price_history` | Fechamentos B3, bases negociada/ajustada e lacunas |
| `get_dividend_history` | Eventos de proventos, datas de pagamento e cobertura |
| `get_shareholding_position` | Posições acionárias e metadados do documento CVM |

As seis ferramentas de consulta de dados aceitam `offset` (padrão 0) e `limit` (padrão 100, máximo
500). As quatro consultas por ticker também exigem `ticker`, por exemplo `BBAS3`.
Listas retornam `items`, `total`, `offset`, `limit` e `next_offset`. Consultas por
ticker mantêm os campos da API e acrescentam `pagination`; a página limita
`history`, `points`, `events` ou `positions`, conforme a ferramenta. O TTM é
retornado completo em cada página de análises.

O recurso `smaug://setup` retorna o mesmo guia de `get_setup_guide`;
`smaug://setup/{platform}/{edition}` seleciona plataforma e modalidade. O guia
funciona com a API desligada. `get_service_status` relata indisponibilidade,
bancos vazios e migração pendente sem executar comandos locais. A fonte é
`src/smaug/shared/setup_guide.md`, também incluída como `SETUP.md` nos pacotes.
Após editá-la, rode `uv run python scripts/render_setup_guide.py` para atualizar
este README; `--check` verifica a sincronização.

A paginação preserva a ordem da API e é aplicada localmente após cada consulta
HTTP completa. Use `next_offset` até receber `null`; páginas sucessivas podem
refletir atualizações ocorridas entre as chamadas. Os resultados têm JSON
estruturado e texto equivalente, preservando datas, valores nulos e suas causas.
Valores decimais são strings para manter a precisão. Esses dados são os
persistidos pelo projeto, não cotações em tempo real.

`MCP_API_BASE_URL` e `MCP_API_TIMEOUT_SECONDS` também podem ser definidos no
`.env`. Erros MCP identificam `api_unavailable`, `api_timeout`,
`invalid_api_response` ou `api_http_error`; erros HTTP preservam o status e o
campo `detail`, incluindo causas como `price_history_not_prepared`.

## Estrutura do repositório

```text
src/smaug/
├── ingestion/     # fontes CVM/B3 e espelho cru em MongoDB
├── analysis/      # domínio, cálculo e persistência dos indicadores
├── portfolio/     # favoritos, identidade, classes e taxonomia
├── shared/        # configuração, conexões, artefatos e eventos
└── entrypoints/   # CLI, API FastAPI e MCP local

frontend/          # aplicação Next.js
alembic/           # migrações do PostgreSQL
tests/             # testes unitários e de integração isolada
```

## Documentação e fonte de verdade

- [`AGENTS.md`](AGENTS.md): regras de engenharia e limites do projeto;
- issues do GitHub: trabalho pendente e próximos passos;
- PRs e histórico do Git: mudanças implementadas e seu contexto;
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
