# Smaug

Análise fundamentalista de ações da B3, com dados públicos da CVM e da B3.
Consulte indicadores, demonstrações financeiras, preços, proventos e composição
acionária em uma base local.

[Instalação](docs/installation.md) · [MCP](docs/mcp.md) · [Desenvolvimento](docs/development.md)

## Instalar

1. Instale o [Docker Desktop](docs/installation.md#instalar-o-docker-desktop)
   e mantenha-o em execução.
2. Baixe o instalador do seu sistema no
   [site de downloads](https://gzucob.github.io/project-smaug/).
   O [guia de instalação](docs/installation.md) explica qual arquivo abrir.
3. Na interface, escolha **Completo** ou **Backend** e clique em **Instalar o Smaug**.
   Aguarde a preparação dos dados e clique em **Abrir o Smaug** ou **Abrir a API**.

| Versão | Inclui |
|---|---|
| **Completo** (`full`) | Interface web, API, MCP e bancos de dados |
| **Backend** (`backend`) | API, MCP e bancos de dados, sem interface web |

As duas versões preparam os mesmos dados diretamente da CVM e da B3, sem escolher
ticker, ano ou documento. A instalação e o uso diário funcionam por cliques;
não é necessário instalar ferramentas de programação ou clonar o repositório.

## Usar e atualizar

Abra **Smaug** pelo atalho do sistema para iniciar/parar os serviços, abrir a
aplicação ou clicar em **Atualizar o Smaug**. Os dados ficam no seu computador.

## MCP

O MCP funciona nas duas versões, com ou sem o frontend. No gerenciador, use
**Conectar ao Codex** ou **Conectar ao Claude Desktop** e siga o
[guia do MCP](docs/mcp.md) para os requisitos e as consultas disponíveis.

## Desenvolvimento

Para executar pelo código ou trabalhar no site de downloads, consulte o
[guia de desenvolvimento](docs/development.md).

## Licença

Licenciado sob a [Apache License 2.0](LICENSE).
