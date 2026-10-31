# Desenvolvimento e operação

[README](../README.md) · [Instalação](installation.md) · [MCP](mcp.md)

Este documento cobre a execução pelo código, operação avançada e desenvolvimento.

## Operação

As duas versões usam o mesmo backend Python, MongoDB para o espelho CVM/B3 e
PostgreSQL para as análises. A versão `full` acrescenta o frontend Next.js.
O MCP consulta a API nas duas versões.

```text
smaug gui
smaug installer --backend
smaug installer --api-port 18000 --web-port 13000
smaug --dir DIRETORIO installer
smaug edition backend
smaug edition full
smaug logs api
smaug logs preparation
smaug backup
smaug restore --backup DIRETORIO_DO_BACKUP
```

Portas são escolhidas na primeira instalação. Use `--dir` antes do comando
para manter instalações independentes. Cada instalação tem seus próprios
volumes; os bancos do checkout não são reutilizados. `restore` substitui os
bancos da instalação selecionada pelo conteúdo do backup.

O gerenciador preserva a configuração durante upgrades. Antes de alterar o
schema PostgreSQL, ele para os escritores e cria um backup. Uma migração
interrompida deixa o estado de recuperação salvo; `smaug upgrade` retoma a
operação pendente. Uma preparação interrompida também pode ser retomada por
`smaug installer`. O processo não apaga dados existentes para recomeçar.

A preparação descobre os arquivos anuais nos índices públicos DFP, ITR e FRE
da CVM. Cada conjunto é ingerido com seus próprios períodos e módulos; os
endpoints B3 de eventos e listagem são consultados em uma etapa separada.
Os leitores também buscam os arquivos FCA e COTAHIST necessários.
O universo usa o contrato FCA configurado na versão do backend.

Os checkpoints e o diagnóstico ficam no volume de cache. Os arquivos das
fontes são preservados por identidade de conteúdo no volume de fontes.
Uma atualização do código refaz a preparação; a ingestão existente compara a
identidade dos arquivos e evita duplicar documentos já espelhados.

## Trabalhar no código

A branch padrão de desenvolvimento é `develop`. Crie branches de trabalho a
partir dela e abra as PRs com destino a `develop`; cada fase do plano de
implementação corresponde a um commit e uma PR, integrada com squash.

Para publicar, abra uma PR de `develop` para `main` e integre com **merge
commit**, preservando as duas branches. Após o sucesso do CI do push na
`main`, a release é publicada automaticamente. Antes do próximo ciclo,
sincronize `develop` com a `main` publicada, preservando o histórico compartilhado.
As duas branches executam CI; somente a `main` publica versões estáveis.

O [plano de instalação com bases preparadas](prepared-database-installation-plan.md)
registra as fases futuras. Sua documentação não significa que as fases de
ingestão, análise, empacotamento ou atualização já estejam implementadas.

Pré-requisitos: Git, Python 3.13, uv e Docker com Compose v2. Para o frontend
ou o site de downloads, use Node.js 24 e npm. Leia o [AGENTS.md](../AGENTS.md)
e as regras do diretório alterado.

```bash
git clone https://github.com/gzucob/project-smaug.git
cd project-smaug
uv sync --frozen
cp .env.example .env
docker compose up -d
uv run alembic upgrade head
uv run smaug prepare
uv run uvicorn smaug.entrypoints.api:app --host 127.0.0.1 --port 8000
```

Ajuste as conexões em `.env` conforme as portas do Compose de desenvolvimento.
`smaug prepare` é o comando Python de preparação dos dados; o executável nativo
`smaug` distribuído nas releases é o gerenciador da instalação Docker. Ele
encaminha os comandos Python pelo subcomando `cli`.

Em outro terminal, para o frontend:

```bash
cd frontend
cp .env.example .env.local
npm ci
npm run dev
```

Acesse <http://localhost:3000>. A API deve continuar em execução em outro terminal.
`frontend/.env.local` define a URL da API; o valor padrão é
`NEXT_PUBLIC_API_BASE=http://localhost:8000`.

As ferramentas de manutenção permitem investigação pontual:

```bash
uv run smaug ingest --help
uv run smaug analyze --help
uv run smaug doctor --all
uv run smaug ingestion-runs --limit 10
uv run smaug ingestion-resume --run-id RUN_ID
uv run smaug formulas
uv run smaug formulas pe_basic --json
```

As mesmas operações podem ser executadas em uma instalação Docker com
`smaug cli`, por exemplo `smaug cli doctor --all`. Elas não são etapas manuais
da instalação normal.

A API documenta seus contratos em `/docs`. `/health` identifica o processo e o
commit; `/ready` verifica os bancos e a migração e informa se há análises.
A prontidão dos serviços é diferente da conclusão da preparação: o gerenciador
só conclui a instalação após ingestão, análise, histórico de preços e validação.

Para desenvolver o MCP a partir do checkout, mantenha a API em execução e rode
`uv run smaug-mcp`. `MCP_API_BASE_URL` seleciona a API. O transporte é stdio;
stdout fica reservado ao protocolo. O guia offline pode ser consultado pela
ferramenta `get_setup_guide` ou pelo recurso `smaug://setup`.

## Verificar alterações

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run lint-imports --no-cache
uv run pytest
uv run python scripts/render_setup_guide.py --check
```

O teste de retenção PostgreSQL requer `SMAUG_TEST_POSTGRES_URI` apontando para
um PostgreSQL de teste e cria um schema isolado. A verificação dos índices de
ingestão usa `SMAUG_TEST_MONGO_URI` e cria um banco MongoDB isolado.
Para alterações no frontend,
execute também `npm run typecheck` e `npm run build` dentro de `frontend`.

## Desenvolver os instaladores

O gerenciador nativo usa Go 1.27.2 e a biblioteca padrão. Go só é necessário
para compilar ou testar o gerenciador.

```bash
cd installer
go test ./...
go vet ./...
cd ..
GITHUB_SHA=$(git rev-parse HEAD) bash scripts/installer/ci-smoke.sh
```

O teste Docker cria bancos descartáveis, oferece arquivos sintéticos no formato
CVM/B3 aos leitores reais e verifica ingestão, cálculo, API, frontend, MCP,
upgrade, troca de versão, backup e restauração. Ele não depende de um dump,
do banco do desenvolvedor ou da disponibilidade das fontes externas.

O workflow de release publica as versões `backend` e `full` para amd64/arm64:

- Windows: executáveis `.exe` com interface gráfica e sem janela de terminal.
- macOS: aplicativos `.app` distribuídos em `.dmg`.
- Ubuntu/Debian: pacotes `.deb` com entrada no menu de aplicativos.

Os executáveis brutos Linux/macOS permanecem disponíveis para automação.
Para execução manual no Linux, marque o arquivo como executável e selecione
explicitamente o comando desejado:

```bash
chmod +x smaug-installer-linux-amd64
./smaug-installer-linux-amd64 installer
```

Sem argumentos, ou com `gui`, o executável abre a interface do gerenciador no
navegador. O servidor temporário usa uma porta aleatória em `127.0.0.1`, uma
sessão secreta, validação de origem e uma lista fechada de ações. A preparação
continua se a aba for fechada; abra o mesmo gerenciador para acompanhar novamente.
Os detalhes são gravados em `installer-ui.log` na pasta da instalação.

| Sistema | Pasta padrão da instalação |
|---|---|
| Windows | `%APPDATA%\smaug` |
| macOS | `~/Library/Application Support/smaug` |
| Linux | `~/.config/smaug` (ou `$XDG_CONFIG_HOME/smaug`) |

Os atalhos acompanham a versão instalada. Comandos explícitos como `installer`,
`upgrade` e `mcp run` continuam operando sem abrir o navegador.

As imagens Docker são fixadas por digest e precisam permitir pull anônimo.
Os executáveis verificam o checksum do pacote interno antes de executá-lo.
O CI verifica o gerenciador nos três sistemas e os containers em Linux
amd64/arm64. Nas PRs, também gera e monta os `.dmg`, confere licença, arquitetura
e checksums, e executa os launchers nativos macOS e Windows com `--help`.
No Windows, verifica ainda abertura, arquivos da interface, sessão única e
encerramento do gerenciador nas duas versões, com um navegador de teste.
Os `.dmg` ficam disponíveis nos artefatos do CI para inspeção.
O empacotamento `.dmg` usa `hdiutil` no runner macOS; os pacotes
`.deb` usam `dpkg-deb`, necessário no host de empacotamento. A preparação
roda na conta do usuário, depois da instalação do pacote.

Os guias de instalação e MCP, também distribuídos nos pacotes, têm uma única fonte:
`src/smaug/shared/setup_guide.md`. Após editá-la, execute
`uv run python scripts/render_setup_guide.py`.

## Site de downloads

`website/` usa React, TypeScript, Vite e Tailwind. O site é estático e não depende
dos bancos ou da API local do Smaug. A marca carrega a mesma fonte Geist do navbar
da aplicação; o arquivo fica em `website/src/assets/` e sua licença em
`website/public/fonts/OFL.txt`.

### Executar localmente

Com Node.js 24 instalado, a partir da raiz do repositório:

```bash
cd website
npm ci
npm run dev
```

Abra <http://localhost:5173/project-smaug/>. Para verificar e visualizar o build:

```bash
npm test
npm run build
npm run preview
```

A prévia fica em <http://localhost:4173/project-smaug/>. Por padrão, os downloads
ficam desabilitados. Para preenchê-los com os arquivos da última release publicada,
execute `npm run prepare:release` antes do build. Esse comando consulta o GitHub e
atualiza `website/public/release.json`; ele exige todos os instaladores e checksums.

### Publicar no GitHub Pages

1. No repositório, selecione **Settings → Pages → Source → GitHub Actions**.
2. Após o merge na `main`, aguarde os workflows **CI** e **Release installers**.
   A release precisa conter os instaladores `.exe`, `.dmg` e `.deb` das duas
   versões e arquiteturas, com seus checksums.
3. O workflow **Publish website** gera os links, compila o site e publica em
   <https://gzucob.github.io/project-smaug/>. Ele também pode ser iniciado
   manualmente pela aba **Actions**.

O workflow roda em alterações do site na `main` e por disparo explícito de
**Release installers**, depois de publicar uma release validada. Esse disparo
seleciona a `main`, mesmo com `develop` como branch padrão do repositório.
Releases incompletas bloqueiam a publicação do site.
Os links de download apontam para arquivos da release verificada; nenhuma
consulta à API do GitHub é necessária no navegador do visitante.
