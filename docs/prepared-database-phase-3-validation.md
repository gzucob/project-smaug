# Retenção por ambiente — fase 3 (#350)

Stack preservada: Python 3.13/uv; MongoDB/Beanie para fontes;
PostgreSQL/SQLAlchemy/Alembic para resultados; FastAPI de leitura; Next.js;
instalador Go/Docker Compose. Nenhuma dependência nova.

## Política automática

O Compose do instalador identifica seu runtime como `installation`. Checkouts e
contêineres de desenvolvimento usam `development` por padrão. Não há opção de
retenção na interface ou na CLI do produto.

Na instalação, `prepare`, ingestão, retomada e backfill utilizam uma área
privada sob `SOURCE_ARTIFACT_DIR/.ingestion/<scope>`. Artefatos SHA-256, ZIPs,
caches CVM/B3, reduções JSON e eventuais extrações ficam nessa área. Chamadas
internas de preparação compartilham a mesma sessão: a admissão de um arquivo
bruto não libera seus bytes enquanto outro consumidor ainda precisa deles.

O escopo identifica a operação, parâmetros e configuração da base. Uma trava
de arquivo exclusiva protege a instalação contra ingestões concorrentes. O
Python dos instaladores roda no contêiner Linux em Windows, Linux e macOS;
a trava é liberada pelo kernel também após `SIGKILL`.

O desenvolvimento continua usando seus diretórios habituais, sem sessão de
limpeza. Arquivos fora da área privada não são varridos nem removidos. Isso
preserva arquivos externos fornecidos à transição `mirror-inputs` e evita
transformar uma ingestão em limpeza de dados anteriores ou não pertencentes à
operação. Nenhuma limpeza do ambiente dev é executada nesta fase.

## Persistência e liberação

`source_acquisitions` preserva observações de aquisição: URL pública, identidade
SHA-256, tamanho, horário de observação/download, ETag, Last-Modified e status
HTTP. A aquisição padrão e o caminho COTAHIST legado da ingestão registram seus
metadados antes da liberação. Relatórios de quarentena também sobrevivem no
MongoDB; uma fonte em quarentena não autoriza remoção.

`source_input_archives` e `source_input_rows` mantêm os contratos de admissão da
fase 1. Antes de liberar a área, a sessão reconcilia todas as cópias ZIP com o
espelho bruto. Falha de CRC, extração, validação ou persistência impede concluir
a sessão. Os checkpoints e a admissão usados para liberar arquivos empregam
write concern `majority` com journal habilitado.

`source_file_sessions` registra operação, tentativas, IDs de execução de
ingestão, arquivos admitidos e os estados:

1. `active`: consumidores ainda podem usar os arquivos.
2. `failed`: operação interrompida; arquivos preservados para retomada.
3. `consumers_complete`: todos os consumidores concluíram com sucesso e os
   insumos estão persistidos; a remoção pode ser retomada independentemente.
4. `cleaned`: remoção concluída.

A limpeza só começa após a operação retornar sucesso, seguida da reconciliação
final dos arquivos. Durante `prepare`, isso inclui suas etapas de ingestão e
verificação. Falhas não marcam admissão parcial como concluída. Repetir a
operação reutiliza sua área; `ingestion-resume` recupera a sessão pelo ID da
execução original. Se a remoção falhar depois do checkpoint, a próxima execução
termina essa remoção antes de reutilizar a área. Cancelar a limpeza não libera
a trava enquanto seu worker de filesystem ainda puder remover caminhos.

Os consumidores de análise continuam exclusivamente nos bancos. Uma aquisição
futura consulta os validadores HTTP no MongoDB; se receber 304 e o arquivo já
tiver sido liberado, refaz o download sem condicionais. Bytes republicados
produzem outra identidade; versões anteriores permanecem no espelho. O cache
em memória só admite um artefato depois que seu observador de persistência
conclui com sucesso.

As sessões são estado operacional, a excluir dos dumps públicos na fase 5
(#352). As observações de aquisição e os fatos admitidos preservam a
proveniência necessária sem depender de caminhos de outra máquina.

## Verificação

`tests/test_source_retention.py` usa bancos temporários e fontes sintéticas ou
o fixture COTAHIST já existente. Cobre múltiplos consumidores, cópias e caches,
persistência parcial, falha de consumidor, cancelamento, `SIGKILL`, concorrência,
retomada pelo ID da ingestão, limpeza interrompida, cancelamento durante remoção,
HTTP 304 sem arquivo, republicação e consulta posterior aos dados MongoDB.

Os testes de seleção verificam que desenvolvimento não inicia a limpeza. O
teste de artefatos impede memoizar um arquivo cujo observador falhou.
`scripts/installer/smoke.py` verifica nas edições Backend e Completa a ausência
de ZIP/CSV/JSON financeiros após a preparação, os checkpoints no MongoDB e a
consulta da análise persistida. Sentinelas externas de backup continuam
preservadas. Os ZIPs sintéticos do instalador têm timestamps fixos para que
repetições da mesma fonte produzam os mesmos bytes.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run lint-imports
SMAUG_TEST_MONGO_URI=mongodb://localhost:27017 \
SMAUG_TEST_POSTGRES_URI=postgresql+asyncpg://smaug:smaug@localhost:5432/smaug \
uv run pytest
```

Esta fase não mede a meta de análise, não gera dumps e não publica uma release.
Essas entregas permanecem nas fases seguintes de #347.
