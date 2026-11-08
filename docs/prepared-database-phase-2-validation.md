# Consultas exclusivas aos bancos — fase 2 (#349)

Stack preservada: Python 3.13/uv; MongoDB/Beanie para fontes;
PostgreSQL/SQLAlchemy/Alembic para resultados; FastAPI de leitura; Next.js;
instalador Go/Docker Compose. Nenhuma dependência nova.

## Contrato implementado

`analyze`, construção de históricos, `doctor`, `report` e resolução para `relink`
utilizam o espelho MongoDB para seus insumos de origem. A API continua servindo
resultados persistidos. A CLI compõe `MongoCompanyRegistry`,
`MongoSecurityHistory`, `MongoCotahistArchive` e `MongoRegistrantResolver`;
nenhum deles recebe cliente HTTP ou caminho de cache.

`MongoSourceInputs` fixa uma revisão admitida por dataset/ano, ordenando por
observação e hash como desempate. A revisão identifica os bytes e a versão do
parser; não é selecionada novamente durante a execução. Somente o contrato
`source-inputs-v1` com admissão completa e validação conhecida é aceito;
COTAHIST exige `as_traded`. As linhas são lidas por arquivo/parser/membro e
ordinal, conferindo continuidade e contagem. Séries incompletas não são
publicadas nem memoizadas.

Cadastro corrente e histórico compartilham as mesmas revisões FCA. O histórico
exige os anos desde 2010 até o último FCA admitido, cadastro geral em cada ano
e valores mobiliários desde 2018. Ausência de um ano obrigatório não é tratada
como evidência de que não existiram outros nomes ou códigos. FCA anterior a
2018 pode não conter o membro de valores mobiliários. Dados CSV vazios e
linhas com campos entre aspas preservam o número de linha física usado nos
achados de identidade.

O leitor MongoDB reduz os campos já extraídos pela ingestão; não reinterpreta
a linha COTAHIST original preservada para auditoria.

As regras de seleção FCA e redução COTAHIST foram extraídas para módulos sem
I/O, compartilhados com os adaptadores anteriores de aquisição. Decimal,
FATCOT, sessões, ISIN, DISMES, ESPECI, sucessão, restatement e retorno total
mantêm suas regras. A recuperação de placeholders lê `LISTING_B3` persistido,
confere código CVM/CNPJ e usa somente códigos de valores mobiliários publicados;
não transforma a raiz do emissor em código negociado.

Fontes ausentes produzem erros nomeados `missing_mirrored_*`; inconsistências
produzem `incomplete_mirrored_*` ou `malformed_mirrored_*`. Os consumidores de
preços mantêm a tradução existente para causas públicas de null. A ausência do
cadastro necessário à seleção do universo encerra o comando com erro de fonte,
sem substituir o universo por uma lista vazia e sem baixar dados.

## Evidência reproduzível

`tests/fixtures/mongo_analysis_reference.json` foi congelado executando a
composição anterior de `develop` em `dcd6d35`, com fontes fixas e relógio/ID
controlados. O conjunto combina cadastro FCA sintético versionado de duas
classes, contas sintéticas DFP/ITR e capital, e o fixture oficial COTAHIST já
existente no repositório. Não contém dados de carteira pessoal.

A comparação cobre os dois tickers de todo esse universo de referência, quatro
visões TTM/ano fechado, todos os indicadores, cobertura, nulls, bases,
proveniência contábil e de mercado e desfechos persistidos. Exclui somente os
campos operacionais `computed_at`, `recorded_at` e `run_id`. Há também comparação
direta entre adaptadores para cadastro, nomes históricos, classes, preços,
estados de direitos e eventos extraídos da série, incluindo FATCOT 1000.

O teste completo bloqueia abertura de arquivos financeiros, ZIP e HTTP. Permite
somente a taxonomia versionada do programa, que é metadado. A integração com
bancos reais importa em MongoDB temporário, remove todos os ZIPs antes da
análise e persiste em schema PostgreSQL temporário; depois lê os resultados
pelo repositório público e compara as entidades. Esses bancos temporários são
removidos ao final. Os dados operacionais de desenvolvimento não são alterados.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run lint-imports
uv run pytest

SMAUG_TEST_MONGO_URI=mongodb://localhost:27017 \
SMAUG_TEST_POSTGRES_URI=postgresql+asyncpg://smaug:smaug@localhost:5432/smaug \
uv run pytest
```

Os testes adicionais cobrem admissão incompleta, parser incompatível,
republicação após fixar revisão, linhas ausentes e lacuna histórica FCA.
`tests/test_analysis_source_boundary.py` percorre as chamadas locais dos
comandos e as dependências dos adaptadores; rejeita reintroduzir aquisição ou
leitura de arquivos por um helper transitivo.

Essa evidência executa a análise completa do universo congelado de teste; não
é uma medição de processamento de todo o mercado no ambiente operacional. A
meta de tempo e a medição completa em cópia isolada pertencem à fase 4 (#351).

## Preparação explícita

`prepare` começa pela etapa retomável `source-inputs`: um caso de uso de ingestão
adquire FCA desde 2010 até o snapshot configurado e COTAHIST desde 1986 até o
ano corrente, valida e persiste os arquivos antes dos consumidores. A cobertura
da fita inclui os códigos antigos que a sucessão precisa confirmar. Essa etapa
usa o armazenamento de artefatos e o parser bruto da fase 1; não calcula
indicadores e não altera a retenção. A análise posterior recebe apenas leitores
MongoDB. O fixture do instalador inclui cabeçalho, rodapé e contagem COTAHIST
válidos para passar pela mesma admissão que uma fonte real.

O teste de integração dessa preparação usa MockTransport e MongoDB temporário,
executa duas vezes, verifica idempotência e consulta cadastro/preço com I/O de
fontes bloqueado. Esse caminho mantém a preparação explícita funcional durante
a transição até sua substituição por importação de bases prontas na fase 6.

## Transição e limites entre fases

Instalações de desenvolvimento com `raw_ingestions` anterior à fase 1 precisam
executar `mirror-inputs` para FCA histórico e COTAHIST disponíveis, conforme o
inventário. A nova análise não converte caches implicitamente. Nenhum arquivo
de desenvolvimento é apagado pela implementação.

A aquisição explícita da ingestão conserva os adaptadores antigos para obter e
interpretar as fontes antes de persistir o espelho. `sync-prices` agora calcula
somente sobre fontes já persistidas; a aquisição de pregões ausentes antes do
recálculo é a fase 7 (#354). Atualização dirigida de fundamentos e coordenação
incremental permanecem nas issues #355 e #356. Esta fase não gera dumps, não
altera retenção e não publica instalador ou release parcial.
