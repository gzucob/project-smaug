# Snapshot público de distribuição — fase 5 (#352)

Stack preservada: Python 3.13/uv; MongoDB/Beanie para fontes;
PostgreSQL/SQLAlchemy/Alembic para resultados; FastAPI de leitura; Next.js;
instalador Go/Docker Compose. A ferramenta usa as dependências existentes do
projeto e os utilitários de dump/restauração das imagens Docker.

## Produção e limites

`scripts/distribution_snapshot.py` produz a base em bancos descartáveis, numa
rede Docker interna. A entrada é um espelho CVM/B3 congelado e identificado
explicitamente pelo operador. A ferramenta copia somente `raw_ingestions`,
`ingestion_validations`, `source_input_archives` e `source_input_rows`.
Não recebe uma conexão com o PostgreSQL de desenvolvimento: migra um banco
vazio e calcula o universo completo pela composição real da CLI.

A cópia do espelho mantém um `fsyncLock` durante a transferência das quatro
coleções. O `finally` libera esse bloqueio também em caso de falha. A origem
não é sanitizada nem limpa. Todos os outros escritores são comandos da própria
ferramenta e terminam antes da exportação. O MongoDB de construção é bloqueado
e o PostgreSQL passa a recusar novas transações de escrita durante os dumps.
Os contêineres e volumes descartáveis pertencentes à execução são removidos
ao final; o espelho de origem e os bancos de desenvolvimento são preservados.

Os fatos públicos, incluindo as linhas originais e os campos dos documentos,
permanecem no espelho. A preparação remove notas livres de aprovação e
metadados de caminhos/máquina dos envelopes de aquisição/validação. Preserva
IDs de artefatos, hashes, regras, decisões, datas, URLs e vínculos de evidência.
Identificadores históricos de runs nos fatos e nos outcomes são proveniência;
as coleções de runs, falhas, sessões, locks e aquisições operacionais não são
copiadas. O snapshot não contém carteira pessoal, configurações de máquina,
status de atualização ou gerações de interface de outra instalação.

As séries diárias são calculadas por uma execução separada de análise,
começando em 2010, conforme o contrato atual da CLI. Essa cobertura de séries
não elimina nenhum ano do espelho B3, nem reduz os períodos dos indicadores.
Gaps e nulls mantêm as causas declaradas pelo cálculo. Os bancos são lidos com
HTTP, ZIPs, arquivos financeiros e conexões externas bloqueados durante os
cálculos e o diagnóstico.

## Artefatos e compatibilidade

A pasta de distribuição contém exatamente:

- `mongo.archive.xz`: dump lógico MongoDB em XZ/LZMA2, com índices e somente as quatro
  coleções públicas.
- `postgres.dump`: dump lógico PostgreSQL em formato custom, comprimido, sem
  owners/ACLs da máquina de construção. Contém o schema migrado e os resultados
  públicos; as tabelas pessoais e operacionais estão vazias.
- `manifest.json`: versão do formato, commit e hash do código de execução,
  revisão Alembic, contrato de cálculo, parser de fontes, versões principais
  dos bancos, revisão dos dados, datas, cobertura, fontes admitidas, tamanhos,
  hashes SHA-256 dos dumps e evidências de validação/restauração.

O hash do código cobre Python e JSON de `src/`, migrações, `pyproject.toml` e
`uv.lock`. Uma mudança de commit provocada por squash/promoção pode preservar
compatibilidade somente se esse conteúdo, schema e contratos forem idênticos.
Uma mudança de fórmula, taxonomia, schema, parser ou dependências exige uma
base compatível. A verificação recusa payloads simbólicos, nomes arbitrários,
tamanhos diferentes, corrupção e formatos incompatíveis antes da importação.
O manifesto só recebe `restore_verified: true` ao completar todas as verificações.

O MongoDB usa XZ/LZMA2, preset 1, CRC64 e dicionário de 1 MiB.
A compressão e a extração usam `lzma` da biblioteca padrão do Python 3.13;
a restauração alimenta `mongorestore --archive` por fluxo, sem criar um dump
descomprimido. A fase 6 pode executar essa extração com o Python já presente
na imagem do backend, sem instalar um descompactador no computador do usuário.
Os dumps são transferidos por streams e arquivos, com memória limitada.
Nenhum ZIP/CSV original, cache financeiro ou diretório de dados físico dos
bancos é distribuído. Dumps e exportações grandes ficam em `.cache/phase5/`,
fora do Git. Os relatórios pequenos versionados acompanham esta documentação.

## Verificação de restauração

A ferramenta restaura em outro par de bancos novos, sem `--drop` ou `--clean`.
Recusa bancos que já contêm coleções/tabelas. Compara contagens e fingerprints
completos: `dbHash` de cada coleção MongoDB e SHA-256 das linhas de cada tabela
pública PostgreSQL, ordenadas por ID. A comparação inclui os valores Decimal
persistidos, os nulls, motivos, proveniência, IDs e datas, sem excluir campos.

Executa `smaug doctor` sobre a restauração, com a fronteira offline ativa.
Depois migra um terceiro PostgreSQL vazio e recalcula todos os indicadores a
partir do MongoDB restaurado. O comparador da fase 4 exige equivalência exata
nos resultados de domínio antes da precisão SQL e nas linhas persistidas,
excluindo somente timestamps e IDs operacionais de execução. O diagnóstico
completo e esse recálculo pertencem à produção do snapshot, não à primeira
abertura do aplicativo.

A medição de importação começa após a verificação de integridade e os testes
de bancos vazios. Inclui os dois restores e a construção dos índices. Uma
sondagem aproximadamente a cada segundo registra o pico de espaço alocado nos volumes; também
são registrados o espaço final e os tamanhos lógicos. Imagens Docker, geração
da base, checksums e diagnóstico têm custos separados desse intervalo.

## Reprodução

Requer um checkout em Linux/WSL2 com acesso à rede bridge do Docker,
Docker em execução, as imagens `mongo:8` e `postgres:17`
disponíveis e um contêiner com o espelho público congelado. O operador deve
identificar a revisão e finalizar a ingestão antes de iniciar a geração.
O diretório de saída e seu diretório irmão `-validation` devem ser novos;
uma falha deixa os artefatos incompletos disponíveis para investigação.

```bash
uv sync --frozen
uv run python scripts/distribution_snapshot.py build \
  --source-container smaug-phase4-mongo --source-database smaug \
  --as-of 2026-11-13T23:00:00+00:00 \
  --output .cache/phase5/public-snapshot

uv run python scripts/distribution_snapshot.py verify \
  .cache/phase5/public-snapshot
```

Um candidato já exportado também pode passar por uma validação independente:

```bash
uv run python scripts/distribution_snapshot.py validate \
  .cache/phase5/public-snapshot --work .cache/phase5/restore-validation
```

O diretório `--work` deve ser novo. A verificação de integridade e compatibilidade
precede a criação dos bancos de restauração.

A extração/importação automática pelo instalador pertence à fase 6 (#353).
A validação dos pacotes finais transferidos/publicados pertence à fase 10
(#357). Esta fase entrega os dumps e o contrato verificável de distribuição;
não publica uma release parcial nem modifica os instaladores.

## Revisão produzida e cobertura

A revisão usa os insumos congelados da fase 4, com o relógio financeiro fixado
em `2026-11-13T23:00:00+00:00`. O código de cálculo corresponde a
`98fd8e5554cbb955d6a803712ba8d077e323fbb3`, revisão Alembic `0044`, contrato
`last_traded_price_v1`. O hash do conteúdo de execução é
`8f8caf9c8dea8f883b688c2ec3baf20d43f05d223dbc4b260976d459eaee4f26`.
A data de publicação do snapshot não transforma essa cobertura em cotação atual.

| Evidência de construção | Resultado |
|---|---:|
| Arquivos admitidos no espelho | 58: FCA 2010–2026 e COTAHIST 1986–2026 |
| Linhas de fontes | 24.742.852 |
| Documentos brutos de fundamentos/eventos | 696.013 |
| Universo selecionado pelo FCA | 433 códigos |
| Tickers com resultados | 430 |
| Skips com motivo / erros | 3 / 0 |
| Visões persistidas | 6.027 |
| Séries diárias / pontos | 433 / 976.138 |
| Lacunas anuais declaradas nas séries | 2.109 |
| Células de indicadores | 470.106 |
| Valores / nulls com motivo / nulls sem classificação | 398.310 / 71.796 / 0 |
| Bloqueios de cobertura de dívida sem classificação | 0 |
| Análise completa de construção, incluindo persistência | 260,752 s |

As lacunas incluem anos em que o papel ainda não tinha uma série resolvida;
permanecem registradas, com causa, no dump. Não houve redução de códigos,
períodos, indicadores ou verificações para diminuir o pacote.
Os hashes do comparador da fase 4 coincidiram com a referência aceita:

- Persistência: `87ba3184de832064115adc8864d11e10a680d16c26015bf0c3555ef129d100db`.
- Domínio Decimal: `1f144b1b9efdef58f5b12fa6f58c72ddd4557fb46bf892c7f3228d4a124a656d`.

A auditoria dos envelopes de metadados não encontrou caminhos locais. A
sondagem da sanitização em banco temporário removeu notas privadas, caminhos
de Linux/Windows e diretórios de cache; preservou URLs, contagens e os fatos
públicos. `portfolio`, `price_refresh_status` e `ui_data_revision` estão vazias.
A fase 6 inicializa as identidades e os estados operacionais da instalação,
incluindo o singleton de revisão da interface.

## Restrição do canal de distribuição

O primeiro dump MongoDB em gzip teve **2.248.870.456 bytes**. Ele sozinho
excede o limite de 2 GiB por arquivo do
[GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
Essa tentativa foi recusada pela ferramenta, antes de marcar o pacote como
validado. O fluxo lógico foi recodificado integralmente em XZ, preservando os
mesmos documentos e índices. A ferramenta final exporta diretamente nesse
formato; não requer a tentativa intermediária em gzip.

| Payload final | Bytes | GiB |
|---|---:|---:|
| `mongo.archive.xz` | 1.421.708.668 | 1,324 |
| `postgres.dump` | 39.633.996 | 0,037 |
| Total dos dumps | 1.461.342.664 | 1,361 |
| Dumps + reserva nativa de 128 MiB | 1.595.560.392 | 1,486 |

O orçamento deixa **551.923.256 bytes** até o limite do canal. A reserva é
conservadora diante dos artefatos da
[release existente](https://github.com/gzucob/project-smaug/releases/tag/2026.10.30-af406a54bf2c):
o maior launcher tem 8.161.280 bytes e o maior bundle de aplicação,
25.798.181 bytes. O empacotamento da fase 6 deve incluir uma única cópia das
bases e respeitar esse orçamento; a fase 10 verifica os arquivos nativos
finais publicados. Não se exige divisão ou extração manual do usuário.

A biblioteca `lzma` e o suporte a CRC64 também foram verificados no Python de
uma imagem local do backend, com rede desabilitada. A medição de geração desta
revisão converteu o stream lógico completo já exportado em 265,717 segundos;
esse é um custo de produção, separado da importação no usuário.

## Condições da medição

A máquina de referência é a mesma da fase 4, verificada novamente em
15/11/2026: Ryzen 5 7600X, 12 threads, Ubuntu/WSL2 com kernel
`6.18.33.2-microsoft-standard-WSL2`, Docker 29.7.1 com 12 CPUs e
16.299.728.896 bytes de RAM disponíveis. MongoDB 8.2.12, com cache WiredTiger
de 2 GiB, e PostgreSQL 17.10 usam as imagens identificadas no manifesto.
Não há quota adicional de CPU nos contêineres.

Cada restauração começa com volumes e bancos vazios. Os dumps já estão no
cache do sistema operacional devido à verificação prévia dos checksums;
os caches do sistema não foram descartados. Os serviços de desenvolvimento
continuam ativos. Os bancos congelados de referência, usados como entrada,
foram devolvidos ao estado parado antes da medição da restauração.
Imagens Docker já estavam disponíveis. Download de imagens/dumps, diagnóstico
e recálculo não fazem parte do tempo de importação.

A primeira restauração concluiu a carga sem falhas, mas a certificação foi
recusada porque o `du` encontrou um arquivo temporário de inicialização do
PostgreSQL que desapareceu durante a sondagem. A correção repete uma leitura
transitória até três vezes e continua recusando falhas persistentes, com testes
para ambos os casos. A medição aceita usa uma nova restauração completa;
a tentativa recusada não é contabilizada como sucesso.

## Restauração aceita

O manifesto validado identifica a revisão
`ae88e00b30f8c76e82b709fdf34047b07bf5c980d733ddf06ae4cc6cd24c183c`.
O intervalo dos resultados é 31/12/2010–30/06/2026 e a maior sessão B3
selecionada nos resultados é 16/10/2026, conforme a consulta aos dados
persistidos. Há 2.488 validações de ingestão, além dos fatos e arquivos já
contabilizados acima.

| Medição de restauração | Resultado |
|---|---:|
| Importação MongoDB + PostgreSQL, incluindo índices | **437,143 s (7min17s)** |
| Documentos MongoDB restaurados / falhas | 25.441.411 / 0 |
| Pico amostrado no volume MongoDB | 7.249.018.880 bytes |
| Pico amostrado no volume PostgreSQL | 216.772.608 bytes |
| Soma dos picos amostrados dos volumes | 7.465.791.488 bytes (6,953 GiB) |
| Picos dos volumes + dumps comprimidos | 8.927.134.152 bytes (8,314 GiB) |
| Volumes após restauração e conferências | 6.839.758.848 bytes (6,370 GiB) |
| Fingerprints integrais MongoDB / PostgreSQL | Idênticos à construção |
| Diagnóstico offline da restauração | Passou; zero nulls sem classificação |
| Recálculo offline a partir do MongoDB restaurado | Equivalência exata; zero erros |

O orçamento de espaço soma picos amostrados por volume e os dois dumps; não
inclui imagens Docker, o instalador original baixado ou outros dados do usuário.
O XZ é descomprimido por fluxo, sem reservar os aproximadamente 25 GiB do
stream lógico descomprimido em um arquivo intermediário. O pico amostrado não
é uma garantia de requisito mínimo para todas as máquinas e plataformas.

O recálculo de equivalência após a restauração levou **388,450 s**, acima da
meta de 300 s da fase 4. A construção limpa levou 260,752 s e os valores,
nulls, cobertura e proveniência coincidiram nas duas execuções. A investigação
controlada dessa diferença está na
[issue #363](https://github.com/gzucob/project-smaug/issues/363), sem atribuir
causa antes de medir. O tempo de recálculo é separado dos 437,143 s de
importação; a instalação não executa esse recálculo completo.

Os relatórios pequenos estão em [`benchmarks/phase-5/`](benchmarks/phase-5/):
manifesto final, construção, compressão, diagnóstico e recomputação restaurada.
Os dumps aceitos estão localmente em `.cache/phase5/public-snapshot/`; os dumps
recusados e as exportações de comparação permanecem fora do Git.

Gates locais finais: Ruff, formatação, mypy, os quatro contratos de arquitetura
e **1.094 testes com MongoDB/PostgreSQL reais** passaram. A bateria inclui
corrupção, compatibilidade de código/schema/contrato, nomes e arquivos extras,
symlinks, recusa de bancos preenchidos, liberação do bloqueio após falha,
compressão com buffers limitados e repetição de sondagens transitórias.
O estado dos checks remotos acompanha a PR.
