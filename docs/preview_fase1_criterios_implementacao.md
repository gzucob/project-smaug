# Fase 1 — Critérios de Implementação (Ingestão de Dados brapi)

**Objetivo da fase:** consumir e persistir os dados fundamentais das ações da
carteira a partir da API brapi, de forma fiel e auditável — e, no mesmo esforço,
**validar se a fundação de dados sustenta a Fase 2** (análise por critérios).

**Escopo explícito:** esta fase NÃO calcula indicadores, NÃO aplica critérios,
NÃO usa LLM. Ingestão e persistência apenas. Qualquer cálculo é Fase 2.

**Carteira-alvo:** PETR4, VALE3, SAPR11, TAEE11, WEGE3 (não-financeiras);
BBAS3, BBDC4 (bancos); BBSE3, CXSE3 (seguradoras).

---

## 1. Princípios que guiam toda a fase

1. **Ingestão fiel, não interpretada.** Guarda-se o que a API devolve, sem
   transformar, normalizar ou derivar. Filtragem e cálculo são Fase 2.
2. **Dado cru é barato de guardar e caro de re-obter.** Persistir a resposta
   completa da API, não só os campos de interesse atual.
3. **Determinismo.** Nenhum LLM nesta fase. Todo o código é testável e previsível.
4. **A fase é também um teste da fundação.** O sucesso não é "rodou sem erro";
   é "confirmei que cada ticker traz os campos que a Fase 2 vai exigir".
5. **Fronteira nítida.** Ingestão (Fase 1) e análise (Fase 2) não se misturam
   no código, para que cada uma seja testável isoladamente.

---

## 2. Critérios de ingestão (o núcleo da Fase 1)

### 2.1 Consumo da API
- Consumir os módulos de **histórico trimestral** necessários para medir
  tendência (ex.: `financialDataHistoryQuarterly`,
  `incomeStatementHistoryQuarterly`, `balanceSheetHistoryQuarterly`,
  `cashflowHistoryQuarterly`), além dos módulos de estatísticas-chave e
  dividendos. Confirmar os nomes exatos na doc no momento da implementação.
- **Autenticação obrigatória:** a maior parte da carteira exige token (só
  PETR4/VALE3/ITUB4/MGLU3 funcionam sem). Portanto, todo o fluxo assume token.
- **Uma responsabilidade por função:** buscar ≠ salvar ≠ relatar. Funções
  separadas para cada etapa.

### 2.2 Profundidade histórica
- Puxar o **máximo de trimestres que o plano gratuito permitir** (não só 3–4).
  A Fase 2 precisa de janela para detectar "N trimestres seguidos"; margem de
  histórico extra é barata agora e cara de re-obter depois.
- Registrar quantos trimestres vieram por ticker (entra no relatório de
  completude, seção 4).

### 2.3 Resiliência mínima (sem overengineering)
- Tratar os erros que a doc do brapi documenta: **401** (token inválido),
  **402/429** (limite excedido), **404** (ticker não encontrado). Cada um pede
  tratamento distinto — parar, esperar, ou pular o ticker.
- **Um ticker que falha não derruba os outros.** Isolar cada consulta; falha
  em BBAS3 não impede a coleta de VALE3.
- **Pausa entre requisições** para respeitar rate limit. Volume é baixo
  (9 ações), então simplicidade > sofisticação; não precisa de retry elaborado
  nesta fase, mas registrar a falha (seção 5).

---

## 3. Critérios de persistência (aqui mora a ponte para a Fase 2)

### 3.1 O que guardar
- **A resposta crua e completa da API**, por ticker e por data de consulta.
  Não descartar campos "que não vou usar" — a Fase 2 revelará necessidades novas.

### 3.2 Metadados obrigatórios junto de cada registro `[ponte Fase 2]`
Todo registro persistido carrega, além do payload:
- **ticker**
- **data/hora da consulta** (quando o dado foi puxado)
- **fonte** (ex.: "brapi", e o módulo/endpoint usado)
- **período de referência** do dado (a que trimestre pertence)

Motivo: a Fase 2 mede *evolução no tempo*. Sem data de consulta e período de
referência gravados, é impossível reconstruir a linha do tempo com honestidade —
e o brapi pode **revisar** dados quando a empresa republica um balanço, então
saber "puxei este número nesta data" é o que permite detectar revisões depois.

### 3.3 Formato e local `[ponte Fase 2]`
- **SQLite** (embutido no Python, zero dependência, dono total dos dados) OU
  arquivos JSON versionados por data. Preferência por SQLite se você já quer
  consultar por ticker/período com facilidade na Fase 2.
- **Idempotência:** re-rodar a coleta não deve corromper nem duplicar
  silenciosamente. Definir chave lógica (ticker + período + data de consulta) e
  decidir a política: append de nova leitura (preserva histórico de revisões) é
  preferível a sobrescrever (que apaga a evidência de revisão).
- **Nunca guardar dado já normalizado/calculado.** O banco da Fase 1 é um
  espelho fiel da fonte; derivações vivem na Fase 2, a partir deste espelho.

### 3.4 Não acoplar o formato de armazenamento à API
- Guardar o payload cru, mas **não** desenhar o resto do sistema assumindo o
  formato específico do brapi. Se um dia a fonte mudar, você quer trocar a
  camada de ingestão sem reescrever a análise. (Ver seção 6.)

---

## 4. Relatório de completude (o que transforma "consumo" em "validação") `[ponte Fase 2]`

Este é o entregável que dá valor real à fase. Após coletar, gerar um relatório
legível que responda, **por ticker**:

- Quais campos/indicadores vieram preenchidos e quais vieram vazios/nulos.
- Quantos trimestres de histórico vieram.
- Se os **campos setorialmente críticos** estão presentes (ver 4.1).
- Data da coleta e eventuais erros por ticker.

### 4.1 Verificação setorial dirigida — o ponto de maior risco `[ponte Fase 2]`
A carteira é ~metade financeira, e é aí que a fundação pode falhar. O relatório
deve checar explicitamente:
- **Bancos (BBAS3, BBDC4):** vieram os campos que critérios de banco exigem
  (ex.: patrimônio no formato de instituição financeira, base para ROE, dados
  ligados a rentabilidade/carteira)? A estrutura contábil difere de uma empresa
  comum — confirmar com os próprios olhos.
- **Seguradoras (BBSE3, CXSE3):** vieram os campos que sustentam ROE e
  resultado do negócio de seguros?
- **Não-financeiras:** vieram receita, lucro, EBITDA, dívida líquida, margens,
  base para dívida líquida/EBITDA?

Se algum campo setorial crítico faltar, **isso é uma descoberta da Fase 1, não
um bug** — e decide se o brapi basta ou se precisará de fonte complementar para
aquele setor. Melhor descobrir agora, barato.

---

## 5. Observabilidade e reprodutibilidade

- **Log do que aconteceu em cada coleta:** tickers consultados, sucesso/falha,
  código de erro, quantidade de trimestres. Não precisa ser sofisticado; precisa
  existir.
- **Segredos fora do código:** token em variável de ambiente / arquivo `.env`
  que **não** entra no controle de versão. Nunca hardcoded, nunca commitado.
- **Reprodutível:** dependências fixadas (requirements). Rodar amanhã deve
  produzir o mesmo comportamento de hoje.
- **Coleta re-executável com segurança:** rodar de novo é uma operação normal
  (você vai coletar a cada trimestre), não um evento arriscado.

---

## 6. Cuidados de arquitetura que facilitam a Fase 2 `[ponte Fase 2]`

Estes não são obrigatórios para "funcionar", mas baratos agora e caros de
retrofitar depois:

1. **Isolar a camada de ingestão atrás de uma fronteira simples.** O resto do
   sistema (Fase 2) deveria pedir "os dados do ticker X no período Y" sem saber
   se vieram do brapi, de cache local, ou de outra fonte. Isso permite trocar ou
   somar fontes (ex.: yfinance como conferência secundária) sem tocar na análise.
2. **Mapa ticker → setor como dado explícito e determinístico.** Já deixar
   registrado a que setor cada ticker pertence (banco/seguradora/utility/
   commodity/indústria). A Fase 2 usa isso para escolher o conjunto de critérios;
   é um "de/para" fixo, nunca inferência semântica.
3. **Separar leitura de dados de interpretação de dados.** A Fase 1 entrega um
   espelho fiel; a Fase 2 lê desse espelho e deriva. Manter essa fronteira
   significa que erros de cálculo (Fase 2) nunca contaminam a base (Fase 1).
4. **Preservar histórico de revisões.** Ao não sobrescrever leituras antigas
   (3.3), você habilita a Fase 2 a detectar quando um balanço foi republicado —
   um sinal qualitativo potencialmente útil.

---

## 7. Definição de "pronto" para a Fase 1

A fase está concluída quando:
- [ ] As 9 ações são coletadas com token, com tratamento de falha por ticker.
- [ ] O dado cru é persistido com ticker, data de consulta, fonte e período.
- [ ] Re-rodar a coleta é seguro e não corrompe o histórico.
- [ ] Existe um relatório de completude por ticker, incluindo a verificação
      setorial dirigida (bancos e seguradoras).
- [ ] Está confirmado, com inspeção real, que os campos que a Fase 2 precisará
      estão presentes — ou está documentado qual campo falta e em qual ticker.
- [ ] Token fora do código; dependências fixadas; log de coleta existente.

O critério de sucesso não é "o script rodou". É **"eu sei, olhando o relatório,
que posso construir a Fase 2 sobre estes dados — ou sei exatamente onde a
fundação tem buraco."**

---

## 8. Fora de escopo nesta fase (para não vazar Fase 2 para dentro da Fase 1)
- Cálculo de indicadores ou tendências.
- Aplicação de qualquer critério de "tese azedando".
- Qualquer uso de LLM / RAG / agente.
- Leitura de RIs em PDF (o RI qualitativo é insumo da Fase 2).
- Decisão de compra/venda (sempre humana, nunca no sistema).
