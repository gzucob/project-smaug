# Inventário de insumos da análise — fase 1 (#348)

Stack preservada: Python 3.13/uv; MongoDB/Beanie para o espelho de fontes;
PostgreSQL/SQLAlchemy/Alembic para resultados; FastAPI de leitura; Next.js;
instalador Go/Docker Compose. Nenhuma dependência nova.

## Fronteira desta fase

Esta fase acrescenta o armazenamento dos insumos brutos que faltavam. Os
leitores de análise ainda usam os adaptadores existentes. A substituição por
consultas exclusivas aos bancos, incluindo a composição das dependências e o
teste sem arquivos/rede, pertence à fase 2 (#349).

## Leituras alcançáveis e representações persistidas

| Consumidores atuais | Insumos públicos | Representação no MongoDB |
|---|---|---|
| `MongoFundamentalsReader`, `MongoSharesReader`, `free_float`, leitores de governança e composição acionária | DFP/ITR/FRE: contas, versões, capital, tesouraria, composição, direitos e eventos CVM | `raw_ingestions`, `ingestion_validations`; adicionalmente, todas as linhas e colunas dos CSVs em `source_input_rows` |
| `CvmCompanyRegistry`, universo, exclusões, data de listagem, classes e units | FCA: cadastro geral e valores mobiliários, versões, CNPJ, código CVM, nomes, códigos e direitos declarados | `source_input_rows`, por arquivo, membro e linha; todas as versões são preservadas, sem selecionar vencedor na ingestão |
| `CvmSecurityHistory`, `CodeSuccession`, identificação de códigos antigos e suas dependências | FCA histórico, nomes empresariais desde 2010 e códigos/classes desde 2018 | Todos os CSVs FCA históricos em `source_input_rows`; não apenas o cadastro corrente |
| `CotahistArchive`, `B3PriceProvider`, `B3BaseChanges`, recuperação de identidade, `CodeSuccession`, históricos | COTAHIST anual: registros completos, sessões, código, mercado, BDI, nome, espécie, preço, fator de cotação, ISIN e estado de direitos | `source_input_rows`: registro original de 245 caracteres e campos `TIPREG`, `DATA`, `CODBDI`, `CODNEG`, `TPMERC`, `NOMRES`, `ESPECI`, `PREULT`, `FATCOT`, `CODISI`, `DISMES`; cabeçalho, rodapé e mercados adicionais são preservados |
| `MongoSharesReader`, `RestatedPriceProvider` | Eventos CVM e B3, contagens declaradas e evidências de resolução/recuperação de identidade | `raw_ingestions`: `CAPITAL`, `CAPITAL_DFP`, `CAPITAL_EVENT`, `CAPITAL_EVENT_B3`, `LISTING_B3`; evidências e validações já registradas pelos adaptadores |
| `MongoCashEventReader`, `DividendAdjustedPriceProvider` | Proventos declarados pela B3, ISIN, datas e preços de referência | `raw_ingestions`: `CASH_DIVIDEND_B3`, com payload original e evidências de identidade; não existe série de retorno total publicada para importar |

Os módulos de eventos/listagem/proventos B3 já consultam HTTP **na ingestão**
e gravam os resultados brutos e evidências no espelho. A fase 1 não introduz
outra fonte nem transfere seu processamento para a análise.
Na identidade idempotente de `LISTING_B3`, o horário de observação acrescentado
pelo adaptador deixa de diferenciar fatos iguais; permanece no snapshot
armazenado. Mudanças no conteúdo B3 continuam criando outra versão. Documentos
históricos não são apagados ou reescritos.

O catálogo de fórmulas, a configuração de taxonomia setorial versionada com o
programa, a lista de tickers descontinuados e a configuração da aplicação são
metadados do programa. Não são arquivos de cotações ou de divulgações
financeiras. A atualização explícita da taxonomia usa a planilha/endpoints B3
e persiste a classificação no PostgreSQL; não participa do cálculo financeiro.
O estado de preparação e seus logs são operacionais. A API e os históricos
publicados continuam lendo resultados já persistidos no PostgreSQL.

## Identidade, admissão e índices

- `source_input_archives` admite somente versões integralmente processadas.
  Identidade única: SHA-256 dos bytes do ZIP + versão do parser. Metadados:
  hash, tamanho comprimido, URL, dataset/ano, observação, modalidade de aquisição,
  metadados HTTP quando disponíveis, contagens por membro e contrato de validação.
  Importações locais registram `local_import`; sua URL é a localização oficial
  esperada, não uma alegação de download HTTP nessa execução.
- `source_input_rows` tem identidade única composta por arquivo + parser + membro
  + ordinal. Reexecuções usam upsert sem alterar fatos existentes. Republicações
  geram outro hash e preservam a versão anterior. Índices por dataset/código/ano/
  sessão e dataset/CNPJ/ano atendem consultas futuras de preço e identidade.
- CRC dos membros, nomes/anos inequívocos, colunas CSV e layout COTAHIST são
  conferidos antes da admissão. Linhas CSV preservam também o cabeçalho e a
  sequência de células: colunas ausentes permanecem ausentes, células vazias
  permanecem vazias e células excedentes são mantidas. Divergências de largura
  publicadas pela CVM são identificadas em `column_count_mismatch`, sem
  completar, descartar ou reinterpretar linhas. Datas, ano da sessão, preço
  codificado, fator de cotação, ordem de cabeçalho/rodapé e total de registros são
  conferidos estruturalmente; valores permanecem strings, sem float ou cálculo.
  Hash e extração usam a mesma cópia privada em disco, criada com memória
  limitada e liberada ao final da tentativa; alterações concorrentes no ZIP
  original não misturam versões durante a escrita. Falhas podem deixar linhas
  parciais, mas nunca um arquivo admitido. Leitores da fase 2 devem selecionar
  somente arquivos admitidos e uma revisão explícita.
- Lotes de no máximo 1.000 linhas limitam a memória da escrita. É necessário
  espaço temporário para uma cópia comprimida de um arquivo por tentativa.
  A ingestão não
  deriva preços ajustados, sucessores, contagens ou indicadores. `PREULT` e
  `FATCOT` permanecem separados; o manifesto identifica COTAHIST como
  `as_traded`. Ajuste societário e retorno total continuam no contexto de análise.

## Transição dos arquivos de desenvolvimento

Com MongoDB disponível e configuração habitual (`MONGO_URI`, `MONGO_DB`), executar:

```bash
uv run smaug mirror-inputs .cache/cvm/fca_cia_aberta_*.zip
uv run smaug mirror-inputs .cache/b3/COTAHIST_A*.ZIP
uv run smaug mirror-inputs .artifacts/sources/sha256/*/*.zip
```

O comando recebe arquivos explícitos, não baixa fontes, não calcula análises e
não remove nem modifica os originais. Os diretórios configurados podem diferir;
usar seus caminhos reais. Os glob patterns são expandidos pelo shell. Arquivos
ZIP não reconhecidos ou mistos causam erro; JSONs de redução não são fontes e
não são importados. Cópias com bytes idênticos são uma única versão no banco,
independentemente do nome/caminho local. A reexecução retoma arquivos incompletos.

A ingestão CVM normal também espelha cada artefato adquirido/aberto pelo seu
observador antes de prosseguir com os consumidores atuais. COTAHIST disponível
no dev usa a transição acima; downloads futuros e a migração dos leitores são
tratados nas fases 2 e 7. Nenhuma política de remoção da fase 3 foi antecipada.

## Verificação

`tests/test_source_inputs.py` verifica fidelidade das linhas, campos necessários
à sucessão, fator por lote, versões FCA, reingestão, interrupção/retomada,
republicação, versão do parser, mudança de bytes e rejeição de dados inválidos.
O conjunto existente de testes de sucessão, restatement e proventos mantém as
invariantes das três bases de preço. Não são usadas cotações de agregadores
como referência. A migração completa do banco dev não é pré-requisito para
instalar o código desta fase; a conclusão de cada arquivo é verificável pelos
metadados persistidos.

Validação adicional com arquivos reais de desenvolvimento: FCA 2026 com 7.769
linhas em dez membros, importado duas vezes em banco temporário (sem duplicação
e com o SHA-256 original preservado); FRE 2025 com 131.078 linhas em 36 membros;
COTAHIST 2015 com 414.179 registros, reconciliados com o total do rodapé. Esses
números descrevem os arquivos usados nesta verificação, não a cobertura atual
da base operacional.
