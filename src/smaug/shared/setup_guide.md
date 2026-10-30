<!-- section:common -->
## Instalar o Smaug

As duas versões incluem bancos de dados, API e MCP e preparam os mesmos dados:

- **full**: inclui a interface web.
- **backend**: funciona sem interface web.

No [site de downloads](https://gzucob.github.io/project-smaug/), selecione a
versão, o sistema e a arquitetura do computador. Se o site não estiver disponível,
baixe os mesmos arquivos em [Releases](https://github.com/gzucob/project-smaug/releases/latest).

<details>
<summary>Nomes dos arquivos para download direto</summary>

| Sistema | full | backend |
|---|---|---|
| Windows x64 | `smaug-installer.exe` | `smaug-backend-installer.exe` |
| Windows ARM64 | `smaug-installer-arm64.exe` | `smaug-backend-installer-arm64.exe` |
| Ubuntu/Debian x64 | `smaug-installer-linux-amd64.deb` | `smaug-backend-installer-linux-amd64.deb` |
| Ubuntu/Debian ARM64 | `smaug-installer-linux-arm64.deb` | `smaug-backend-installer-linux-arm64.deb` |
| macOS Intel | `smaug-installer-macos-amd64.dmg` | `smaug-backend-installer-macos-amd64.dmg` |
| macOS Apple Silicon | `smaug-installer-macos-arm64.dmg` | `smaug-backend-installer-macos-arm64.dmg` |

</details>

### Instalar o Docker Desktop

Recomendamos o **Docker Desktop**: ele inclui Docker e Compose e oferece uma
interface para iniciar e parar os containers, sem precisar usar o terminal.

1. Baixe o Docker Desktop e siga o instalador do seu sistema:
   [Windows](https://docs.docker.com/desktop/setup/install/windows-install/),
   [macOS](https://docs.docker.com/desktop/setup/install/mac-install/) ou
   [Linux](https://docs.docker.com/desktop/setup/install/linux/).
2. No Windows, use o backend **WSL 2**, com containers Linux, e aceite a
   ativação dos componentes solicitados pelo Docker Desktop. Reinicie o
   computador se solicitado. Se o Docker informar que o WSL está ausente ou
   desatualizado, siga a [configuração oficial do WSL](https://docs.docker.com/desktop/setup/install/windows-install/#wsl-verification-and-setup).
3. Abra o Docker Desktop e aguarde o Docker Engine ficar em execução antes de
   instalar ou iniciar o Smaug. Mantenha o Docker Desktop em execução durante o uso.

Se optar pelo instalador Linux dentro do WSL, habilite sua distribuição em
**Settings → Resources → WSL Integration** no Docker Desktop. Essa integração
é separada do requisito de ter WSL 2; ela não é necessária para executar o
instalador `.exe` do Smaug com dois cliques no Windows.

Depois da instalação do Smaug, abra a aba **Containers** do Docker Desktop e
localize o grupo `smaug-…`. Use **Start** para iniciar o grupo e **Stop** para
pará-lo. Consulte o [guia da interface de containers](https://docs.docker.com/desktop/use-desktop/container/).

Não é necessário instalar Python, Node, Go ou clonar o repositório.

Abra o instalador baixado conforme o seu sistema. O gerenciador aparece no
navegador e funciona somente no seu computador. Não é necessário digitar comandos.

<!-- section:linux -->
### Linux e WSL

No Ubuntu ou Debian com ambiente gráfico, abra o arquivo `.deb` no instalador
de aplicativos do sistema e confirme a instalação. Depois, abra **Instalar Smaug**
(ou **Instalar Smaug Backend**) no menu de aplicativos.

Os pacotes `.deb` são destinados a Ubuntu/Debian. Em outros sistemas Linux,
use os executáveis descritos na
[referência técnica](https://github.com/gzucob/project-smaug/blob/main/docs/development.md).
Se você usa WSL, prefira abrir o instalador `.exe` pelo Windows.

<!-- section:macos -->
### macOS

Abra o arquivo `.dmg` da arquitetura do seu Mac: `arm64` para Apple Silicon ou
`amd64` para Intel. Arraste **Smaug Installer** (ou **Smaug Backend Installer**)
para **Applications** e abra o aplicativo.

<!-- section:windows -->
### Windows

Abra `@@INSTALLER@@.exe` com dois cliques. Para Windows ARM64, use
`@@INSTALLER@@-arm64.exe`. O gerenciador abre automaticamente no navegador.

<!-- section:data -->
### Aguarde a preparação dos dados

No gerenciador, escolha **Completo** ou **Backend** e clique em **Instalar o Smaug**.

O instalador baixa os componentes, cria os bancos, busca os arquivos publicados
pela CVM e os dados B3 usados pelo Smaug, ingere todo o universo suportado e
calcula as análises e o histórico de preços. Você não precisa escolher ticker,
ano ou tipo de documento. Cada instalação constrói a própria base a partir
das fontes públicas.

A primeira preparação pode levar horas e precisa de internet. A interface mostra
a atividade e oferece os detalhes da operação. Se houver uma interrupção, abra
o gerenciador e clique em **Retomar instalação**: o trabalho concluído é preservado.
Lacunas nas fontes continuam identificadas pelas suas causas; dados ausentes
não viram zero.

Ao concluir, clique em **Abrir o Smaug** na versão completa ou **Abrir a API**
na versão backend. Os endereços padrão são <http://localhost:3000> para a
aplicação e <http://localhost:8000/docs> para a API. Somente a
própria máquina acessa essas portas.

<!-- section:operations -->
### Usar a instalação

Depois da instalação, abra **Smaug** pelo menu Iniciar no Windows, pelo menu de
aplicativos no Linux ou por `~/Applications/Smaug.app` no macOS.
Use **Iniciar**, **Parar** e **Abrir o Smaug** (ou **Abrir a API**) na interface.
O botão **Fechar gerenciador** encerra apenas essa janela de controle; os serviços
continuam como estavam. Mantenha o Docker Desktop em execução durante o uso.

Se fechar a aba durante a preparação, abra o mesmo atalho para acompanhar a
operação. Os detalhes ficam em **Ver detalhes da operação** e no arquivo
`installer-ui.log` da instalação.

Para automação, o comando `smaug` fica disponível em um novo terminal:
`smaug gui`, `smaug start`, `smaug stop` e `smaug check`. Use `smaug installer`
para retomar a preparação ou `smaug logs preparation` para o log da ingestão.

<!-- section:maintenance -->
### Atualizar

Abra o Smaug pelo atalho e clique em **Atualizar o Smaug**. A interface avisa
quando encontrar uma versão disponível. A atualização começa quando você clica
no botão. Para automação, o mesmo fluxo está disponível pelo terminal:

```text
smaug upgrade
```

A atualização busca a versão publicada, prepara os dados e inicia os serviços,
preservando a configuração da instalação.

Backup, restauração e portas personalizadas ficam na
[referência técnica](https://github.com/gzucob/project-smaug/blob/main/docs/development.md).

<!-- section:mcp -->
## MCP

O MCP está disponível nas duas instalações: **full** e **backend**. Ele consulta
diretamente a API e funciona com ou sem o frontend. Permite consultar análises,
histórico de preços, proventos e composição acionária já persistidos no Smaug.

Mantenha o Docker Desktop e os serviços do Smaug em execução. No gerenciador,
clique em **Iniciar** se necessário e use **Conectar ao Codex** ou
**Conectar ao Claude Desktop**. Reinicie o cliente após conectar.

O registro automático do Codex requer o Codex CLI instalado no computador.
Para Claude Desktop, instale o aplicativo; o registro automático está disponível
no Windows e no macOS.

Para automação, registre no Codex pelo terminal:

```text
smaug mcp register --client codex
```

Para Claude Desktop, use `smaug mcp register --client claude`. Para obter a
configuração manual, use `smaug mcp config --client codex` ou `--client claude`.

### Ferramentas disponíveis

| Ferramenta | Função |
|---|---|
| `get_setup_guide` | Consultar o guia de instalação, inclusive sem a API disponível |
| `get_service_status` | Verificar a disponibilidade da API e dos dados |
| `list_portfolio` | Listar os tickers favoritados |
| `list_analysis_summaries` | Listar os resumos das análises persistidas |
| `get_ticker_analysis` | Consultar a análise de um ticker |
| `get_price_history` | Consultar o histórico de preços |
| `get_dividend_history` | Consultar o histórico de proventos |
| `get_shareholding_position` | Consultar a composição acionária |

O MCP é de consulta: não instala, atualiza, ingere dados ou altera os bancos.
Para iniciar os serviços, use **Iniciar** no gerenciador ou `smaug start` no terminal.

Exemplos de pedidos no cliente: “Consulte a análise de PETR4” e
“Mostre o histórico de proventos de BBAS3”.
