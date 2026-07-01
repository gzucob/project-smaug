# Regras de Issue

Uma issue sem prefixo ou sem os labels obrigatórios é considerada incompleta.

## Título
`[NAMESPACE-NN] título curto no imperativo (até 72 chars)`

- `NAMESPACE`: código da tabela abaixo. `NN`: sequência de dois dígitos.
- Título em inglês, no imperativo.

### Namespaces
| Namespace | Área |
|---|---|
| `ING` | Ingestão — cliente brapi, coleta, persistência do espelho |
| `PORT` | Portfólio — mapa ticker → setor |
| `CORE` | Shared — config, conexão Mongo, EventBus, erros |
| `INFRA` | Docker, dependências, configuração do repositório |
| `DX` | Ferramentas, experiência de dev local |
| `TEST` | Testes, cobertura, CI |
| `DOCS` | Documentação |
| `SEC` | Segurança — segredos, token, exposição |

## Labels obrigatórios (os três)
- area: `area: ingestion`, `area: portfolio`, `area: core`, `area: infra`,
  `area: docs`, `area: testing`
- priority: `priority: high`, `priority: medium`, `priority: low`
- type: `type: feature`, `type: bug`, `type: tech-debt`, `type: security`,
  `type: docs`, `type: chore`

Uma issue pode ter mais de um `area:`; `priority` e `type` são únicos.

## Corpo
```
## Contexto
## Melhoria / Correção
## Notas de implementação (opcional)
```

## Fechamento
No corpo do PR: `Closes #NN` (o GitHub fecha a issue ao mergear).
