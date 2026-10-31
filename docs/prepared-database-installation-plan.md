# Plano de instalação com bases preparadas

Status: plano aprovado em escopo. Fase 0 integrada; fase 1 autorizada para
implementação em #348, vinculada à tarefa mãe #347. As fases 2–10 dependem de
uma instrução posterior de execução.

## Objetivo e decisões

Entregar uma experiência próxima à instalação de um aplicativo desktop:
baixar um instalador, importar uma base pronta e abrir a plataforma. A primeira
abertura não depende de consulta à CVM/B3, ingestão, recálculo financeiro ou
diagnóstico completo. O download inicial das imagens Docker, quando ausentes,
continua sendo um custo separado dos dados.

Stack preservada: Python 3.13 e uv; MongoDB/Beanie para dados de origem;
PostgreSQL/SQLAlchemy/Alembic para resultados; FastAPI de leitura; Next.js;
instalador Go com Docker Compose. CVM e B3 permanecem as únicas fontes públicas,
sem credenciais. O processamento e a persistência são executados pela CLI.

- Somente a ingestão lê os arquivos de origem. Todos os dados de origem
  necessários às consultas e aos cálculos devem estar no MongoDB, incluindo
  identidade, códigos históricos, cotações, eventos e evidências de seleção.
- Leitores de aplicação e de análise consultam MongoDB; resultados derivados
  continuam no PostgreSQL. Não existe fallback para arquivos ou HTTP nos
  leitores de análise. Metadados e configurações do programa não são arquivos
  de origem financeira.
- Na instalação do usuário, arquivos de origem são temporários: após todos os
  consumidores concluírem extração, validação e persistência, os arquivos e
  suas cópias locais são removidos. No desenvolvimento, são preservados na
  estrutura do projeto. Essa diferença de retenção não altera os leitores.
- A distribuição contém somente dumps lógicos de MongoDB/PostgreSQL e um
  manifesto. Não contém ZIPs/CSVs CVM/B3 nem caches de séries em arquivos.
- A base é produzida em ambiente limpo, com dados públicos e análises
  validadas. Não é um backup direto do ambiente pessoal de desenvolvimento.
- Os dumps comprimidos vêm dentro do instalador nativo. O usuário não escolhe
  dumps, não extrai arquivos manualmente e não executa comandos de restauração.
- O instalador extrai/importa por fluxo para disco, com memória limitada, e
  remove os dumps temporários após sucesso. Não mantém o pacote inteiro em RAM.
- A conferência local verifica integridade, compatibilidade, sucesso da
  importação e serviços. Não repete a análise nem o diagnóstico financeiro
  completo já executado antes da publicação.
- Depois que a aplicação estiver utilizável, cotações e fundamentos podem ser
  atualizados em paralelo, sem bloquear o uso. O recálculo considera apenas
  resultados afetados e uma revisão consistente dos insumos.
- `sync-prices` atualiza pregões/cotações. A busca de fundamentos tem fluxo
  próprio, dirigido a divulgações esperadas e pendentes, por empresa e período;
  não é acoplada ao `sync-prices` nem uma varredura periódica indiscriminada.
- Atualizações recuperam o período offline e tratam reapresentações. Uma falha
  mantém os resultados válidos anteriormente publicados na instalação.
- Fórmulas, Decimal, identidade dos emissores, rastreabilidade, causas de null
  e as três bases de preços permanecem preservados. Dados ausentes não viram
  zero, aproximação ou exclusão silenciosa de empresas.

## Entrega e branches

Cada fase abaixo corresponde a **um commit lógico e uma PR** de uma branch de
trabalho para `develop`, com squash. A próxima fase começa após a integração
da anterior e parte de `develop` atualizada. Correções antes da integração
devem ser consolidadas no commit da fase, respeitando a segurança do histórico.

`develop` é a branch padrão de desenvolvimento. `main` contém releases.
Uma PR de release promove `develop` para `main` com **merge commit**, sem
apagar as branches permanentes. Após o CI do push na `main` passar, a release
é publicada automaticamente. Essa PR de promoção é separada das PRs das fases
e preserva os commits delas. O site publica código da `main` e instaladores
estáveis verificados. Antes do próximo ciclo, sincronizar `develop` com a
`main` publicada, sem reescrever o histórico compartilhado.

As fases de instalação só são promovidas para release quando o conjunto
estiver funcional e validado. Nenhuma publicação parcial pode apresentar um
pacote como pronto se ainda depender de arquivos de origem ausentes.

## Fases de implementação

### Fase 0 — Separar desenvolvimento e releases

Commit/PR: `chore(ci): separate development and release branches`.

Criar `develop` a partir da `main` atual, ajustar CI para pushes e PRs em ambas,
manter publicação estável restrita ao CI de push confiável da `main` e adaptar
a publicação do site à troca da branch padrão. Atualizar as regras de trabalho,
habilitar merge commit para promoções e proteger as branches permanentes.

Aceitação: `develop` existe, é a branch padrão e tem proteção; CI valida PRs
para `develop` e `main`; desenvolvimento não publica releases; o fluxo da
`main` mantém publicação automática e o site seleciona explicitamente `main`.
Não implementar ingestão, recálculo, dumps ou instalação nas fases seguintes.

### Fase 1 — Completar o espelho de insumos no MongoDB

Commit/PR: `feat(ingestion): persist all analysis source inputs in MongoDB`.

Inventariar todas as leituras de fontes feitas pela análise e por suas
dependências. Persistir os insumos faltantes sem interpretação financeira,
incluindo COTAHIST as traded e seus campos necessários, identidade FCA,
histórico de códigos e eventos. Preservar versões, hashes, origem e validações
no banco; definir identidade idempotente e índices adequados. Preparar uma
ingestão de transição a partir dos arquivos disponíveis no ambiente dev.

Aceitação: o inventário não tem insumos obrigatórios apenas em arquivos;
reingestão não duplica fatos; evidências de sucessão e bases de preços se
reconciliam com as fontes. Arquivos existentes do dev são preservados.

### Fase 2 — Consultar exclusivamente os bancos

Commit/PR: `refactor(analysis): read source inputs exclusively from MongoDB`.

Substituir os leitores de arquivos/HTTP usados por análise, resolução de
identidade, histórico e consultas por adaptadores MongoDB. Downloads e parsing
ficam na ingestão. Ausência no espelho gera a causa correspondente; não dispara
um download implícito. Adequar a composição da CLI e seus consumidores.

Aceitação: análise completa e consultas funcionam com rede desabilitada e
cache/arquivos de origem inacessíveis; resultados e proveniência equivalem à
referência congelada. Um teste de fronteira impede regressões desse contrato.

### Fase 3 — Aplicar retenção por ambiente

Commit/PR: `feat(ingestion): remove extracted source caches in user installs`.

Separar retenção de desenvolvimento e instalação sem expor uma escolha ao
usuário. Remover todas as cópias temporárias de cada arquivo somente após os
consumidores e a persistência terem concluído. Guardar no MongoDB metadados de
aquisição, validação e conclusão necessários à retomada e à identificação de
republicações. Tratar interrupções sem marcar ingestão parcial como concluída.

Aceitação: ingestão bem-sucedida do usuário deixa os dados consultáveis sem
arquivos de origem; o dev retém os arquivos; falhas e retomadas não perdem dados
nem deixam dependências de caminhos já apagados. Não executar limpeza no dev.

### Fase 4 — Reduzir o processamento completo de análise

Commit/PR: `perf(analysis): reuse source reads and batch persisted results`.

Medir separadamente consultas, transformação, cálculo e persistência usando
uma cópia isolada da base. Priorizar eliminação de leituras/transformações
duplicadas e transações por resultado; aplicar concorrência limitada segundo
o gargalo medido, com controle de memória e preservação da ordenação lógica.
O resultado das medições acompanha a PR.

Aceitação: análise completa, incluindo persistência, abaixo de 300 segundos em
execuções repetidas na mesma máquina de referência, com hardware, recursos
Docker, cobertura e estado de caches registrados. Sem rede/downloads, sem
reduzir tickers, períodos, indicadores ou verificações de correção. Comparar
valores, nulls, cobertura e proveniência; excluir da equivalência apenas campos
operacionais como horário e identificador da execução. Se a meta não for
atingida, registrar o resultado e resolver o gargalo antes de concluir a fase.

### Fase 5 — Gerar uma base limpa de distribuição

Commit/PR: `feat(distribution): export validated public database snapshots`.

Produzir a base em ambiente controlado, com fontes completas e resultados
validados. Exportar dumps lógicos consistentes após parar escritores. Incluir
somente dados públicos, resultados, vínculos de proveniência e validações
necessários. Excluir carteira pessoal, configuração da máquina, locks e estados
transitórios de execução. Dados publicados não carregam caminhos locais como
dependência. Vincular dumps à versão de código, schema, contrato de cálculo,
revisão dos dados, datas, cobertura, tamanhos e hashes por manifesto.

Aceitação: restaurar os dumps em bancos vazios reproduz a cobertura e os
resultados; não há dados pessoais nem dependência de arquivos. Medir tamanho
comprimido, espaço de restauração e tempo de importação. Confirmar que o pacote
nativo final cabe no canal de distribuição antes de integrar o empacotamento;
se não couber, resolver essa restrição sem exigir extração manual do usuário.

### Fase 6 — Embutir e importar a base nos instaladores

Commit/PR: `feat(installer): bundle prepared databases for immediate startup`.

Incluir o mesmo snapshot lógico compatível nas edições Completa e Backend e
nos pacotes nativos de cada plataforma/arquitetura. Extrair/importar com memória
limitada, checksums, compatibilidade e retomada segura. Inicializar identidades
e estados operacionais próprios da instalação; nunca reutilizar estado de
outra máquina nem sobrescrever dados pessoais de uma instalação existente.

Aceitação: baixar um pacote e executá-lo basta; não há seleção manual de dumps,
arquivos financeiros extras, ingestão ou análise na instalação. A importação
concluída libera uso com a base publicada e remove os dumps temporários. A
checagem curta detecta importação interrompida/corrompida. Medir o pico de RAM
da instalação: 1 GB é uma hipótese do usuário, não um limite já comprovado.
Imagens Docker ausentes continuam com download automático identificado.

### Fase 7 — Atualizar cotações pela ingestão e MongoDB

Commit/PR: `feat(prices): ingest missing B3 sessions before refreshing analyses`.

Adequar `sync-prices` para obter B3, persistir os fatos no MongoDB, liberar
arquivos temporários e calcular somente usando os bancos. Recuperar pregões
desde a cobertura do snapshot, inclusive após períodos offline. Manter o
tratamento de preços, sucessão e eventos societários consistente entre série
histórica e indicadores. Não buscar resultados trimestrais nesse comando.

Aceitação: os pregões ausentes são recuperados; nenhuma leitura financeira de
arquivo ocorre na análise; atualização falha preserva resultados válidos e
não transforma lacunas em números de outra base.

### Fase 8 — Buscar fundamentos pendentes de forma dirigida

Commit/PR: `feat(ingestion): synchronize pending financial disclosures`.

Criar fluxo próprio que compare a cobertura importada com divulgações esperadas
por empresa/período e identifique reapresentações. Formalizar janelas e política
de tentativas limitadas para períodos pendentes, sem varreduras indiscriminadas
em toda inicialização ou no ciclo de `sync-prices`. Registrar as pendências e
conclusões no banco para não repetir trabalho já concluído.

Aceitação: snapshot atualizado não provoca reconstrução histórica; uma
divulgação nova/reapresentação é incorporada idempotentemente; pendências não
publicadas ficam registradas e tentativas respeitam a janela definida. Se as
fontes não permitirem a identificação pretendida, discutir a estratégia antes
de introduzir um polling genérico.

### Fase 9 — Coordenar atualização e recálculo incrementais após abertura

Commit/PR: `feat(analysis): refresh affected results after background ingestion`.

Iniciar os fluxos independentes de cotações e fundamentos após a plataforma
ficar utilizável. Permitir buscas em paralelo com limites de recursos e
coordenação de escritores; selecionar insumos de uma revisão consistente e
recalcular os resultados afetados, com publicação segura. Não acoplar a
prontidão da aplicação ao sucesso das fontes. Não recalcular todo o universo
em uma atualização apenas de interface ou quando nenhum insumo mudou.

Aceitação: a aplicação abre offline com a base publicada; atualização não
bloqueia nem degrada excessivamente o uso; falhas preservam a última base
válida. Mudanças trimestrais ou de cotação atualizam todas as dependências
afetadas, incluindo classes relacionadas e séries, sem alterações parciais
inconsistentes visíveis ao usuário.

### Fase 10 — Validar e publicar o fluxo completo

Commit/PR: `test(installer): verify prepared database installation end to end`.

Completar a validação dos pacotes finais, inclusive após transferência entre
jobs e publicação/download, em Windows/Linux/macOS e arquiteturas suportadas.
Cobrir instalação limpa, interrupção/retomada, falta de espaço, operação offline
após obtenção de componentes, atualização de uma instalação existente e
ausência de cache. Atualizar documentação e metadados do site para o fluxo real.

Aceitação: pacote baixado instala e abre com cobertura publicada; distribuição
não expõe dados pessoais; caches do usuário são removidos e os do dev
preservados; análise não acessa arquivos/rede. Registrar download inicial,
importação, checagem, tempo até uso, pico de RAM e espaço em disco separadamente.
O tempo até uso não inclui espera por atualização das fontes ou recálculo.
Executar os gates aplicáveis e somente então promover a release para `main`.

## Limites das metas

O tempo total da instalação ainda depende do tamanho dos dumps, importação e
obtenção das imagens Docker. Não há promessa de duração específica antes dessas
medições. A meta de análise completa inferior a cinco minutos é distinta do
tempo de atualização incremental e da instalação, que não executa análise.
O indicador visual de progresso não é escopo desta implementação.
