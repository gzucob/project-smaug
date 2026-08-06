# Roadmap

## Objetivo

Uma plataforma de análise fundamentalista sobre **todos os tickers da B3**,
alimentada pelos dados abertos da CVM e da própria B3. O portfólio pessoal é um
foco de uso do produto, não uma amostra privilegiada nem um oráculo de correção.

A correção é demonstrada por reconciliações com os insumos primários, fórmulas
declaradas e invariantes de domínio. Casos de teste são escolhidos pela forma dos
dados — regime contábil, fatia individual ou consolidada, *units*, múltiplas
classes, tesouraria, eventos societários, período e base de preço — e não por
pertencerem a uma lista curada.

## Princípio de ordem

**Estabilizar antes de crescer.** Nenhum indicador novo entra sem uma fórmula
declarada, reconciliação dos insumos CVM/B3 e comportamento de nulo testado.
Escalar um cálculo errado para centenas de empresas só multiplica o erro.

Daí a ordem M0 → M1 → M2 → M3: primeiro **saber o que é verdade**, depois
**estar certo**, depois **rodar em escala**, depois **interpretar**.

---

## M0 — Confiabilidade

> *Saber o que é verdade.*

O estado dos dados não vive em prosa. Cobertura e causas de nulo são calculadas
a partir da análise persistida, para que uma mudança no espelho ou no cálculo
apareça no relatório em vez de envelhecer num documento.

Escopo: relatório de cobertura, coleta de preços, higiene do backlog, modelo de
documentação.

**Gate:** `smaug doctor --all` — um relatório de cobertura sobre a análise
persistida — reporta um **status conhecido para todo indicador**: ou um valor, ou
um nulo com causa nomeada.

"Nulo com causa nomeada" é o coração do M0. Hoje um nulo num banco pode ser três
coisas indistinguíveis: julgamento de domínio (o indicador não se aplica a
banco), lacuna do nosso mapeamento, ou contagem de ações ausente. Enquanto forem
indistinguíveis, não há como afirmar que o sistema está certo.

---

## M1 — Fidelidade às fontes

> *Estar certo.*

Os indicadores reconciliam com os dados primários da CVM e da B3 e com as
fórmulas declaradas pelo domínio, **provado por testes** — não por comparação
com saídas sem linhagem de agregadores.

Escopo:

- Testes de fórmulas e reconciliação para balanço, margens, valores por ação,
  dividendos e capitalização por classe.
- Fixtures reais apenas quando apontam explicitamente para o artefato CVM/B3 de
  origem; casos sintéticos quando isolam uma propriedade do domínio.
- **Gating por regime contábil** (padrão / BACEN / SUSEP) substitui o enum
  `Sector` de cinco valores. A aplicabilidade de um indicador é uma propriedade
  do plano de contas que a empresa usa, não do seu setor econômico —
  `is_financial` é um proxy grosseiro disso hoje.

**Gate:** a suíte de invariantes passa para todas as formas de dados suportadas,
e todo indicador inaplicável é inaplicável por regime declarado, não por exceção
de ticker.

---

## M2 — Escala B3

> *Rodar em escala.*

Ingestão em lote de todas as companhias listadas.

Escopo:

- **Taxonomia da B3**: setor econômico + subsetor + segmento, extraídos do
  registro da CVM.
- **Registro de companhias**: substitui os mapas curados à mão
  (`TICKER_TO_CVM_CODE`, `TICKER_TO_CNPJ`), que não escalam para a bolsa inteira.
- Ingestão em lote.

O código de M1 já é **projetado para lote** (registro de companhias, taxonomia)
antes de **rodar em lote** — e cada forma nova de dado precisa entrar na suíte de
invariantes antes de ser aceita em escala.

**Gate:** ingestão e análise de todas as companhias listadas, `smaug doctor
--all` sem nulos não classificados e nenhuma regressão na suíte de fórmulas e
invariantes. O `doctor` cobre escala; não substitui os testes de valores não
nulos.

---

## M3 — Pipeline de IA

> *Interpretar.*

Pipeline de análise por IA sobre o portfólio, apoiado em dados cuja fidelidade
já é garantida por testes (M1) e cuja cobertura é conhecida (M0).

Escopo definido quando M1 fechar.

---

## Onde o resto mora

Este arquivo é a **direção**, não o estado. Seguindo
`docs/AGENTS.md`:

| Pergunta | Onde |
|---|---|
| O que falta? | As issues do GitHub, cada uma em seu milestone |
| Por que escolhemos assim? | `docs/adr/` |
| O que é verdade agora? | `smaug doctor` e os testes — nunca um documento |
| Como o projeto aprendeu o que sabe? | O histórico do git, e as *Consequences* de cada ADR |
