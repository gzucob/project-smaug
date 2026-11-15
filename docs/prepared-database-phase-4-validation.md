# Análise completa offline — fase 4 (#351)

Stack preservada: Python 3.13/uv; MongoDB/Beanie para fontes;
PostgreSQL/SQLAlchemy/Alembic para resultados; FastAPI de leitura; Next.js;
instalador Go/Docker Compose. Nenhuma dependência nova.

## Escopo e método

A referência executa o código de `e67a35ff9fc9e236c67ea7ddadb82e44a0eb7a7f`
(fase 3 integrada) sobre uma cópia isolada do espelho. A comparação executa a
mesma CLI e o universo inteiro selecionado pelo FCA congelado: **433 códigos**.
Não há filtro de ticker, redução de períodos ou seleção de indicadores.

Foram copiados 696.013 documentos de `raw_ingestions`, as validações existentes
e o FCA 2010–2026. A ingestão explícita admitiu os 41 COTAHIST disponíveis,
1986–2026, somente na cópia. São 58 arquivos admitidos e 24.742.852 linhas de
fonte. A fonte de preços mais recente desse conjunto termina em 15/10/2026;
esta é uma medição de uma revisão congelada, não uma cotação atual.

Os bancos usados foram `smaug-phase4-mongo` e `smaug-phase4-postgres`, em rede
Docker interna. Os bancos e os arquivos de desenvolvimento foram preservados.
Cada execução usa um PostgreSQL vazio, criado a partir do mesmo template já
migrado; resultados de outra execução não podem mascarar perda de cobertura.
MongoDB e PostgreSQL são reiniciados antes de cada medição. Os caches da
aplicação começam vazios; o cache de arquivos do sistema operacional não é
descartado. Os demais serviços locais de desenvolvimento permanecem ligados.

`scripts/profile_analysis.py` executa a composição real de `analyze`. O relógio
financeiro é fixado em `2026-11-13T23:00:00+00:00`; o tempo decorrido usa relógio
monotônico. O intervalo inclui seleção do universo, consultas, transformação,
cálculo, persistência e formatação final da CLI. A ingestão inicial, a criação
dos bancos e a exportação/comparação dos resultados ficam fora desse intervalo.

Durante a análise, as conexões são limitadas aos endereços e portas dos bancos;
HTTP e abertura de ZIP são bloqueados. Leituras financeiras de ZIP/CSV/TXT/JSON
também são bloqueadas, preservando apenas a taxonomia versionada do programa.
O teste de fronteira da fase 2 continua verificando as dependências transitivas.

As execuções com `--breakdown` medem tempos exclusivos **dentro de cada tarefa**
para consultas, transformação, fórmulas e persistência. Esperas de tarefas
concorrentes se sobrepõem; suas durações não devem ser somadas como tempo total
da execução. O relatório explicita essa sobreposição. A instrumentação acrescenta
custo, sobretudo ao leitor original
que percorre cada linha no Python. As repetições de aceitação usam o comando
sem instrumentação e exigem estritamente menos de 300 segundos.

O comparador exporta JSONL por fluxo, tanto das entidades de domínio quanto
das linhas persistidas. Compara Decimal antes da precisão numérica do
PostgreSQL e depois da persistência, nulls, motivos, cobertura e proveniência.
Somente `computed_at`, `recorded_at` e `run_id` são excluídos; IDs das linhas
persistidas, datas das fontes e bases financeiras permanecem na comparação.

## Mudanças

- Cadastro, sucessão, eventos de capital e preços compartilham uma única fita
  B3 por execução. A recuperação de placeholders não provoca uma segunda
  leitura e redução de todos os anos.
- O MongoDB confere contagem, limites e integralidade dos ordinais e estrutura
  de todas as linhas COTAHIST. A unicidade arquivo/parser/membro/ordinal já
  faz parte do contrato do espelho. O Python recebe apenas observações à vista
  e os campos usados pelo redutor. Linhas ignoradas pelos preços continuam
  participando da validação e permanecem no espelho bruto.
- Demonstrativos de um ticker são transformados uma vez para `history()` e
  `annuals()`. Cada demonstrativo selecionado é deduplicado uma vez, com a
  identidade comum do arquivo construída uma vez. Nomes normalizados usam
  LRU de 8.192 entradas. O cache transformado retém até 16 tickers;
  a resolução CPC 41 de uma classe não é reutilizada em outra classe.
- Capital, eventos e proventos usam caches pertencentes aos leitores da
  execução, com limite de 16 chaves por cache. Ausências resolvidas podem ser
  reutilizadas; erros e cancelamentos não são memoizados. Novas execuções
  constroem novos leitores.
- As visões de cada ticker são gravadas em uma transação, com até 50 linhas
  por statement. TTM e exercícios fechados preservam seus índices parciais.
  Travas transacionais por ticker, adquiridas em ordem estável antes das
  linhas, coordenam lotes e escritas pontuais. O `WHERE computed_at <= ...`
  continua impedindo uma execução antiga de sobrescrever resultado mais novo.
  Uma falha em qualquer statement reverte o lote inteiro.

Há até três anos B3 em leitura/redução e até quatro tickers em preparação.
Os cálculos continuam no mesmo event loop; as consultas são sobrepostas.
A publicação é sequencial, na ordem original dos tickers, preservando também
os IDs das linhas. Uma falha operacional cancela e aguarda as preparações
pendentes, que não executam a publicação dos resultados.

Também são reutilizados os documentos brutos de governança, as entradas de
reconciliação de capital e as tabelas de fatores de restatement. A deduplicação
de células usa chaves tipadas, preservando os campos originais, e a seleção de
imposto de renda evita percorrer repetidamente os mesmos candidatos.
O ajuste por proventos reutiliza fatores apenas entre datas ex consecutivas,
preservando a ordem dos eventos e das operações Decimal da média original.

## Resultados e equivalência

| Execução | Instrumentação | Tempo total | CPU Python | Pico RSS Python (KiB) |
|---|---|---:|---:|---:|
| Referência, fase 3 | Com breakdown | 1.950,409 s (32min30s) | Ver JSON | 803.240 |
| Código final, perfil | Com breakdown | 301,452 s | 275,494 s | 933.336 |
| Código final, repetição 1 | Sem breakdown | **293,412 s (4min53s)** | 266,838 s | 889.828 |
| Código final, repetição 2 | Sem breakdown | **280,534 s (4min41s)** | 254,858 s | 883.156 |

As duas repetições passaram no limite estrito de 300 s. A referência é
instrumentada; os tempos de aceitação não são. Portanto, a comparação não
estabelece uma razão precisa de aceleração entre execuções sem instrumentação.
A margem menor foi de 6,588 s e depende das condições registradas abaixo.

Todas as execuções produziram 430 tickers analisados, três skips com motivo,
zero erros, 433 outcomes e 6.027 visões persistidas. As exportações completas
coincidiram byte a byte. Os hashes SHA-256 comuns são:

- Persistência: `87ba3184de832064115adc8864d11e10a680d16c26015bf0c3555ef129d100db`.
- Domínio antes da precisão SQL: `1f144b1b9efdef58f5b12fa6f58c72ddd4557fb46bf892c7f3228d4a124a656d`.

| Etapa instrumentada | Referência (s) | Código final (s) |
|---|---:|---:|
| Consultas e consumo dos cursores | 635,372 | 610,506 |
| Transformação | 1.262,130 | 177,851 |
| Fórmulas | 1,239 | 1,210 |
| Persistência | 48,692 | 200,618 |

As etapas do código final incluem esperas concorrentes sobrepostas e não
somam seu tempo total. Consultas incluem decodificação e iteração no cliente;
a contagem de chamadas do perfil não representa comandos enviados ao MongoDB.
A persistência pode aguardar recursos enquanto outras tarefas preparam dados.

| Execução | Pico MongoDB (bytes) | Pico PostgreSQL (bytes) |
|---|---:|---:|
| Referência | 4.639.080.448 | 243.167.232 |
| Código final, perfil | 2.498.428.928 | 248.537.088 |
| Repetição 1 | 2.727.399.424 | 243.646.464 |
| Repetição 2 | 2.758.373.376 | 260.378.624 |

O RSS Python é coletado antes das exportações, ao encerrar a CLI. Os picos dos
contêineres são lidos depois da exportação e comparação; incluem esse trabalho
e cache de páginas, sendo limites superiores conservadores para a análise.

Os relatórios pequenos estão versionados em
[`benchmarks/phase-4/`](benchmarks/phase-4/): `baseline.json`,
`accepted-profile.json`, `accepted-1.json` e `accepted-2.json`.
As exportações JSONL e os dumps completos permanecem locais, fora do Git.

## Verificação

A bateria completa com MongoDB e PostgreSQL reais cobre isolamento dos caches,
repetição após falhas/cancelamentos, integridade de todas as linhas de fonte,
limites de concorrência, publicação ordenada, rollback e proteção contra
sobrescrita antiga, além da equivalência Decimal do ajuste por proventos.
Validação local final: 1.064 testes passaram com os dois bancos reais; Ruff,
formatação e mypy passaram; os quatro contratos do import-linter foram mantidos. O estado dos checks remotos consta na PR.

## Máquina de referência

Medições realizadas em 13–14/11/2026, na mesma máquina:

| Recurso | Configuração |
|---|---|
| CPU | AMD Ryzen 5 7600X, 6 núcleos / 12 threads disponíveis |
| Sistema | Ubuntu 26.04 LTS em WSL2, kernel 6.18.33.2 |
| Python | CPython 3.13.14, ambiente uv do projeto |
| Docker | Engine 29.7.1, 12 CPUs, 16.299.732.992 bytes de RAM disponíveis |
| MongoDB | MongoDB 8.2.12, imagem `mongo:8`, WiredTiger com cache configurado de 2 GiB |
| PostgreSQL | PostgreSQL 17.10, imagem `postgres:17`, configuração padrão |
| Cotas dos contêineres | Sem limite adicional de CPU ou RAM |

Identidades das imagens:

- MongoDB: `sha256:e0ce8c35124d4a9f9785532d1f268f39e9728ffa1cb38f46fa482436424c4bd3`.
- PostgreSQL: `sha256:7958605b474b3d264a969cb3a123d6aa00ad1e1fe9da8a69984dabb704d93317`.

A soma das contagens declaradas dos membros admitidos foi reconciliada com as
24.742.852 linhas armazenadas. O dump de entrada anterior à admissão B3 tem
SHA-256 `96943ebbfec602b61dd8e45f6db9d01be0396ad12b97182ef617a0c7c9b31784`;
o manifesto local congelado tem SHA-256
`f155b8636f62eab103d08695c9b28d06098646992dc33431e5cd11d99753581b`.
Os artefatos operacionais e as exportações completas ficam em `.cache/phase4/`.

## Compatibilidade encontrada nas fontes reais

A preparação identificou dois bloqueios no parser bruto anterior:

- O COTAHIST 2007 contém quatro registros do mercado `070` com FATCOT zero.
  Esses fatos são preservados; o mercado à vista `010` continua rejeitando
  fator zero. Nenhum desses registros de opções participa dos preços à vista.
- Os arquivos locais 2025/2026 declaram no trailer somente o número de
  cotações, enquanto os anteriores contam também cabeçalho e trailer.
  A admissão reconcilia exatamente uma dessas duas contagens, preservando os
  bytes declarados e o total físico de linhas. Diferenças de outras magnitudes
  e truncamentos continuam rejeitados.

O [layout B3, seção 3.3](https://www.b3.com.br/data/files/33/67/B9/50/D84057102C784E47AC094EA8/SeriesHistoricas_Layout.pdf)
especifica a contagem inclusiva. A variante observada não é apresentada como
alteração documentada desse layout. O ZIP 2025 foi novamente obtido da
[publicação B3](https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A2025.ZIP)
e coincide byte a byte com o arquivo local, SHA-256
`6cc0ad851c8f605ca30418acac48be81f1767f6ed05b4f563772d06ae448047d`.
Ele contém 3.174.700 linhas físicas e declara 3.174.698 cotações. O arquivo
local de 2026 contém 3.202.913 linhas e declara 3.202.911 cotações.

Os ZIPs originais não foram corrigidos ou regravados. A admissão preserva todos
os registros, inclusive cabeçalho, trailer e mercados não usados nos preços.
Essas correções viabilizam a medição completa sem excluir anos problemáticos.

## Reprodução

Prepare cópias isoladas, admita os insumos faltantes pela ingestão e finalize
todos os escritores antes de medir. Migre um template PostgreSQL vazio e crie
um banco novo a partir dele para cada execução. Configure `MONGO_URI`,
`MONGO_DB` e `POSTGRES_URI` explicitamente para esses bancos descartáveis.

```bash
mkdir -p /tmp/smaug-phase4/baseline
git archive e67a35ff9fc9e236c67ea7ddadb82e44a0eb7a7f src | \
  tar -x -C /tmp/smaug-phase4/baseline
PYTHONPATH=/tmp/smaug-phase4/baseline/src \
  uv run python scripts/profile_analysis.py --isolated \
  --as-of 2026-11-13T23:00:00+00:00 --breakdown \
  --output /tmp/smaug-phase4/baseline.json

# Outro PostgreSQL vazio, com URI atualizada no ambiente; bancos reiniciados.
uv run python scripts/profile_analysis.py --isolated \
  --as-of 2026-11-13T23:00:00+00:00 --breakdown \
  --reference /tmp/smaug-phase4/baseline.json \
  --output /tmp/smaug-phase4/profile.json

# Outro PostgreSQL vazio; MongoDB/PostgreSQL reiniciados antes de cada repetição.
uv run python scripts/profile_analysis.py --isolated \
  --as-of 2026-11-13T23:00:00+00:00 \
  --reference /tmp/smaug-phase4/baseline.json --max-seconds 300 \
  --output /tmp/smaug-phase4/repetition.json
```

Para medir a referência, extraia `src/` da revisão acima com `git archive` para
um diretório separado e aponte `PYTHONPATH` para esse `src/`, usando a mesma
ferramenta e os mesmos bancos congelados. A ferramenta recusa PostgreSQL com
resultados existentes e falha se houver erros, divergência de hashes/escopo
ou tempo igual ou superior ao limite solicitado. `--ticker` serve somente a
sondagens; não foi usado nas medições completas.
