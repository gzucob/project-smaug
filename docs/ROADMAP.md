# Roadmap

## Objetivo

Uma plataforma de análise fundamentalista sobre **todos os tickers da B3**,
alimentada pelos dados abertos da CVM, com **nove tickers do portfólio como foco
de análise** — são eles que provam que o cálculo está certo antes de o cálculo
rodar em escala.

Os nove: `PETR4`, `VALE3`, `SAPR11`, `TAEE11`, `WEGE3`, `BBAS3`, `BBDC4`,
`BBSE3`, `CXSE3` (`portfolio/domain/cvm_codes.py`).

Eles não são uma amostra aleatória: cobrem os três regimes contábeis que a CVM
publica (padrão, BACEN, SUSEP), duas *units* (`SAPR11`, `TAEE11`) e um caso de
desdobramento (`BBAS3`). Um indicador que está correto para os nove está correto
para a maior parte da bolsa.

## Princípio de ordem

**Estabilizar antes de crescer.** Nenhum indicador novo entra enquanto a
cobertura dos nove não for conhecida e verde. Escalar um cálculo errado para 400
empresas só multiplica o erro — e o torna invisível, porque ninguém confere 400
empresas à mão.

Daí a ordem M0 → M1 → M2 → M3: primeiro **saber o que é verdade**, depois
**estar certo**, depois **rodar em escala**, depois **interpretar**.

---

## M0 — Confiabilidade

> *Saber o que é verdade.*

Hoje não existe uma resposta confiável para "quais indicadores dos nove tickers
estão preenchidos, e por que os outros não estão". A verdade sobre o estado dos
dados vivia em prosa — num log de achados escrito à mão, hoje aposentado (#43) —
e prosa não recalcula: 38 de 45 exercícios fechados perderam o preço sem ninguém
notar.

Escopo: relatório de cobertura, coleta de preços, higiene do backlog, modelo de
documentação.

**Gate:** `smaug doctor` — um relatório de cobertura sobre a análise persistida —
reporta, para cada exercício fechado dos nove tickers, um **status conhecido para
todo indicador**: ou um valor, ou um nulo com causa nomeada.

"Nulo com causa nomeada" é o coração do M0. Hoje um nulo num banco pode ser três
coisas indistinguíveis: julgamento de domínio (o indicador não se aplica a
banco), lacuna do nosso mapeamento, ou contagem de ações ausente. Enquanto forem
indistinguíveis, não há como afirmar que o sistema está certo.

---

## M1 — Fidelidade dos 9

> *Estar certo.*

Os nove tickers batem com as plataformas de referência (AUVP Analítica,
Investidor10), **provado por um teste** — não por um parágrafo.

Escopo:

- Fixture com os valores das plataformas de referência para os nove tickers,
  commitada no repositório; um teste compara nosso cálculo contra ela com
  **tolerância por indicador**. Uma divergência vira uma falha visível de teste.
- **Gating por regime contábil** (padrão / BACEN / SUSEP) substitui o enum
  `Sector` de cinco valores. A aplicabilidade de um indicador é uma propriedade
  do plano de contas que a empresa usa, não do seu setor econômico —
  `is_financial` é um proxy grosseiro disso hoje.

**Gate:** o teste de fidelidade passa para os nove, e todo indicador
inaplicável é inaplicável por regime declarado, não por exceção codificada.

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
antes de **rodar em lote** — mas só roda depois que os nove estiverem fiéis.

**Gate:** ingestão e análise de todas as companhias listadas, sem regressão no
teste de fidelidade dos nove.

---

## M3 — Pipeline de IA

> *Interpretar.*

Pipeline de análise por IA sobre os nove tickers, apoiado em dados cuja
fidelidade já é garantida por teste (M1) e cuja cobertura é conhecida (M0).

Escopo definido quando M1 fechar.

---

## Onde o resto mora

Este arquivo é a **direção**, não o estado. Seguindo
`.claude/RULES/RULES_DOCS.md`:

| Pergunta | Onde |
|---|---|
| O que falta? | As issues do GitHub, cada uma em seu milestone |
| Por que escolhemos assim? | `docs/adr/` |
| O que é verdade agora? | `smaug doctor` e os testes — nunca um documento |
| Como o projeto aprendeu o que sabe? | O histórico do git, e as *Consequences* de cada ADR |
