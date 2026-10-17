<!-- section:common -->
## Instalação e execução com Docker

Modalidade deste guia: **@@EDITION@@**. `backend` inclui MongoDB,
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

<!-- section:linux -->
### Linux e WSL

Baixe `smaug-@@EDITION@@-VERSAO.tar.gz` e
`smaug-@@EDITION@@-VERSAO.tar.gz.sha256`. Na pasta dos downloads:

```bash
sha256sum --check smaug-@@EDITION@@-VERSAO.tar.gz.sha256
mkdir -p smaug-installer
tar -xzf smaug-@@EDITION@@-VERSAO.tar.gz -C smaug-installer
bash smaug-installer/install.sh --edition @@EDITION@@
smaugctl() { "${XDG_CONFIG_HOME:-$HOME/.config}/smaug/smaugctl" "$@"; }
```

A instalação padrão fica em `~/.config/smaug`, ou em `$XDG_CONFIG_HOME/smaug`
quando configurado. No WSL, o Docker precisa estar acessível dentro da
distribuição Linux. A função acima permite usar os próximos comandos neste
terminal; o instalador também informa o caminho completo do gerenciador.

<!-- section:macos -->
### macOS

Baixe `smaug-@@EDITION@@-VERSAO.tar.gz` e
`smaug-@@EDITION@@-VERSAO.tar.gz.sha256`. Na pasta dos downloads:

```bash
shasum -a 256 --check smaug-@@EDITION@@-VERSAO.tar.gz.sha256
mkdir -p smaug-installer
tar -xzf smaug-@@EDITION@@-VERSAO.tar.gz -C smaug-installer
bash smaug-installer/install.sh --edition @@EDITION@@
smaugctl() { "$HOME/Library/Application Support/smaug/smaugctl" "$@"; }
```

A instalação padrão fica em `~/Library/Application Support/smaug`. A função
acima permite usar os próximos comandos neste terminal. O macOS virtual usado
para validação precisa conseguir executar o engine de containers Linux.

<!-- section:windows -->
### Windows

Baixe `smaug-@@EDITION@@-VERSAO.zip` e
`smaug-@@EDITION@@-VERSAO.zip.sha256`. Use Docker Desktop em modo de containers
Linux. Na pasta dos downloads, execute no PowerShell:

```powershell
$smaugArchive = 'smaug-@@EDITION@@-VERSAO.zip'
$smaugExpected = ((Get-Content -LiteralPath "$smaugArchive.sha256") -split '\s+')[0]
$smaugActual = (Get-FileHash -Algorithm SHA256 -LiteralPath $smaugArchive).Hash
if ($smaugActual -ne $smaugExpected) { throw 'Checksum diferente do publicado' }
Expand-Archive -LiteralPath $smaugArchive -DestinationPath '.\smaug-installer'
& .\smaug-installer\install.ps1 --edition @@EDITION@@
function smaugctl { & (Join-Path $env:APPDATA 'smaug\smaugctl.ps1') @args }
```

A instalação padrão fica em `%APPDATA%\smaug`. A função acima permite usar
os próximos comandos neste terminal. Os scripts verificam também os arquivos
extraídos antes de executar o gerenciador. A execução do script deve seguir a
política PowerShell do seu ambiente; o instalador não altera essa política.

<!-- section:operations -->
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

<!-- section:data -->
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

<!-- section:maintenance -->
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

<!-- section:mcp -->
### Instalar e conectar o MCP

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

<!-- platform:linux -->
No **Linux/WSL**, na pasta dos downloads:

```bash
sha256sum --check smaug-mcp-VERSAO.tar.gz.sha256
mkdir -p smaug-mcp-installer
tar -xzf smaug-mcp-VERSAO.tar.gz -C smaug-mcp-installer
bash smaug-mcp-installer/install.sh --api-base-url http://127.0.0.1:8000 --client codex
smaugmcp() { "${XDG_CONFIG_HOME:-$HOME/.config}/smaug-mcp/smaugctl" "$@"; }
```

<!-- /platform -->

<!-- platform:macos -->
No **macOS**:

```bash
shasum -a 256 --check smaug-mcp-VERSAO.tar.gz.sha256
mkdir -p smaug-mcp-installer
tar -xzf smaug-mcp-VERSAO.tar.gz -C smaug-mcp-installer
bash smaug-mcp-installer/install.sh --api-base-url http://127.0.0.1:8000 --client both
smaugmcp() { "$HOME/Library/Application Support/smaug-mcp/smaugctl" "$@"; }
```

<!-- /platform -->

<!-- platform:windows -->
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
<!-- /platform -->

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
