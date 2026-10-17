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
verifica os dois pacotes em amd64/arm64 antes de disponibilizar uma release.
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
### Conectar o MCP e obter ajuda

O conector MCP atual é instalado a partir do checkout com Python 3.13 e uv:
execute `uv sync` na raiz do projeto. Configure seu cliente MCP para iniciar
`uv --directory /CAMINHO/ABSOLUTO/project-smaug run smaug-mcp` via `stdio`.
Veja os exemplos de configuração de clientes no
[README](https://github.com/gzucob/project-smaug#mcp-local).

Configure `MCP_API_BASE_URL` com a URL da API que deseja consultar. O padrão
é `http://127.0.0.1:8000`; na instalação de teste acima, use
`http://127.0.0.1:18000`. Essa é a URL da API, não a porta do frontend.
Quando cliente e conector estão em sistemas diferentes, como Windows e WSL,
use caminhos e endereços acessíveis ao processo que executa o conector.

Depois de conectado, o MCP fornece `get_setup_guide` e o recurso
`smaug://setup` mesmo com a API desligada. `get_service_status` consulta
somente `/health` e `/ready`: verifica a API e as dependências que ela relata,
não inspeciona Docker ou o frontend e não inicia serviços. Resultados de
falha indicam como consultar este guia e usar `smaugctl` no host correto.

O guia continua disponível no README e como `SETUP.md` nos pacotes de release
antes de o próprio MCP estar conectado. Nenhuma dessas leituras instala,
calcula indicadores ou executa manutenção. Essas ações permanecem nos
comandos CLI descritos acima.
