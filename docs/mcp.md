# MCP

[README](../README.md) · [Instalação](installation.md) · [MCP](mcp.md) · [Desenvolvimento](development.md)

<!-- BEGIN SMAUG SETUP GUIDE -->

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

<!-- END SMAUG SETUP GUIDE -->
