# Instalação

[README](../README.md) · [Instalação](installation.md) · [MCP](mcp.md) · [Desenvolvimento](development.md)

<!-- BEGIN SMAUG SETUP GUIDE -->

## Instalar o Smaug

Os dois instaladores incluem a mesma base pública preparada. As duas versões
oferecem bancos de dados, API e MCP:

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

### Linux e WSL

No Ubuntu ou Debian com ambiente gráfico, abra o arquivo `.deb` no instalador
de aplicativos do sistema e confirme a instalação. Depois, abra **Instalar Smaug**
(ou **Instalar Smaug Backend**) no menu de aplicativos.

Os pacotes `.deb` são destinados a Ubuntu/Debian. Em outros sistemas Linux,
use os executáveis descritos na
[referência técnica](https://github.com/gzucob/project-smaug/blob/main/docs/development.md).
Se você usa WSL, prefira abrir o instalador `.exe` pelo Windows.

### macOS

Abra o arquivo `.dmg` da arquitetura do seu Mac: `arm64` para Apple Silicon ou
`amd64` para Intel. Arraste **Smaug Installer** (ou **Smaug Backend Installer**)
para **Applications** e abra o aplicativo.

### Windows

Abra `smaug-installer.exe` com dois cliques. Para Windows ARM64, use
`smaug-installer-arm64.exe`. O gerenciador abre automaticamente no navegador.

### Aguarde a preparação dos dados

No gerenciador, escolha **Completo** ou **Backend** e clique em **Instalar o Smaug**.

O instalador importa a base incluída no pacote, com os dados públicos CVM/B3,
as análises e o histórico de preços já calculados. O Docker baixa automaticamente
as imagens que faltarem. A interface mostra a atividade e os detalhes da
operação.

Se a importação for interrompida, reabra o instalador original e clique em
**Retomar instalação**. O instalador recupera o projeto criado para essa
instalação e refaz a carga parcial quando necessário.

Ao concluir, clique em **Abrir o Smaug** na versão completa ou **Abrir a API**
na versão backend. Os endereços padrão são <http://localhost:3000> para a
aplicação e <http://localhost:8000/docs> para a API. Somente a
própria máquina acessa essas portas.

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
`smaug gui`, `smaug start`, `smaug stop` e `smaug check`. O instalador original
também aceita `installer` para retomar a importação pelo terminal.

### Atualizar

Abra o Smaug pelo atalho e clique em **Atualizar o Smaug**. A interface avisa
quando encontrar uma versão disponível. A atualização começa quando você clica
no botão. Para automação, o mesmo fluxo está disponível pelo terminal:

```text
smaug upgrade
```

A atualização busca a versão publicada e inicia os serviços com os bancos,
a carteira e a configuração da instalação existente.

Backup, restauração e portas personalizadas ficam na
[referência técnica](https://github.com/gzucob/project-smaug/blob/main/docs/development.md).

<!-- END SMAUG SETUP GUIDE -->
