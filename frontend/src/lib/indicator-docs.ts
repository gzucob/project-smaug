/**
 * Reference documentation for each indicator: the formula as Smaug actually
 * computes it, what it measures, and where it carries (or loses) meaning across
 * the B3 sector taxonomy.
 *
 * Two things worth stating up front, because they explain the shape of this file:
 *
 * - **The formulas mirror `analysis/domain/calculator.py`, not the textbook.**
 *   Where the two diverge (ROE over closing equity, ROIC over a flat 34% tax
 *   rate) the divergence is recorded in `caveat` rather than smoothed over.
 * - **Relevance is expressed against B3's subsectors**, which are finer than the
 *   five sectors Smaug classifies internally. `naSectors` is the narrower,
 *   mechanical fact: the sectors for which the calculator returns `None`.
 */
import type { IndicatorKey, SectorKey } from "@/lib/types";

/** A subsector (or family of them) plus the reason the indicator behaves that way. */
export interface RelevanceNote {
  where: string;
  why: string;
}

export interface IndicatorDoc {
  /** Formula as computed, in PT-BR prose-math. Rendered in tabular figures. */
  formula: string;
  /** What the number measures, and how to read it. */
  what: string;
  strongIn: RelevanceNote[];
  weakIn: RelevanceNote[];
  /** Sectors where the calculator deliberately returns nothing. */
  naSectors?: SectorKey[];
  /** A fidelity note: where Smaug's number can differ from a reference platform. */
  caveat?: string;
}

/** Banks and insurers file under a financial-institution structure. */
const FINANCIAL: SectorKey[] = ["bank", "insurer"];

export const INDICATOR_DOCS: Record<IndicatorKey, IndicatorDoc> = {
  // ------------------------------------------------------- rentabilidade ---
  roe: {
    formula: "Lucro líquido (anualizado) ÷ Patrimônio líquido",
    what: "Quanto de lucro a empresa extrai de cada real de capital próprio. É a medida mais direta da eficiência do capital dos sócios.",
    strongIn: [
      {
        where: "Intermediários Financeiros (bancos), Previdência e Seguros",
        why: "o capital próprio é a matéria-prima do negócio — é ele que limita quanto o banco pode emprestar ou a seguradora pode subscrever",
      },
      {
        where: "Serviços Financeiros Diversos, Securitizadoras",
        why: "mesma lógica: o retorno sobre o capital regulatório é o placar do setor",
      },
    ],
    weakIn: [
      {
        where: "Construção Civil, Transporte (aéreo), empresas em recuperação",
        why: "com patrimônio líquido muito baixo ou negativo o índice explode ou inverte de sinal, sem significado econômico",
      },
      {
        where: "Programas e Serviços, Comércio",
        why: "recompras agressivas encolhem o PL e inflam o ROE sem que a operação tenha melhorado",
      },
    ],
    caveat:
      "Smaug divide pelo PL de fechamento do período, não pelo PL médio do período. Em anos de forte emissão ou recompra o número diverge de plataformas que usam a média. Este ROE usa a fatia dos controladores nos dois lados da fração; a variante consolidada é o roe_total (ADR 0026).",
  },
  roe_total: {
    formula: "Lucro líquido consolidado (anualizado) ÷ Patrimônio líquido consolidado",
    what: "O mesmo ROE, medido sobre o grupo inteiro: lucro e patrimônio incluem a participação dos acionistas minoritários das controladas. É a base que as plataformas de referência publicam como ROE.",
    strongIn: [
      {
        where: "Grupos com controladas relevantes não integrais (bebidas, mineração, software)",
        why: "responde 'quanto o grupo consolidado rende', enquanto o roe responde 'quanto rende a fatia do acionista da holding' — em grupos integrais os dois coincidem",
      },
    ],
    weakIn: [
      {
        where: "Empresas sem minoritários relevantes",
        why: "é numericamente igual ao roe — a coluna só acrescenta informação onde há participação minoritária material",
      },
    ],
    caveat:
      "Numerador e denominador vêm da mesma fatia (o total consolidado), nunca misturados com a fatia dos controladores — a regra de pareamento do ADR 0026.",
  },
  roa: {
    formula: "Lucro líquido (anualizado) ÷ Ativo total",
    what: "Retorno gerado por cada real de ativo, independente de como esse ativo foi financiado (dívida ou capital próprio).",
    strongIn: [
      {
        where: "Intermediários Financeiros, Securitizadoras",
        why: "o ativo (a carteira de crédito) é literalmente o motor da receita",
      },
      {
        where: "Energia Elétrica, Água e Saneamento, Transporte",
        why: "negócios de ativo pesado, onde a produtividade do imobilizado é a questão central",
      },
    ],
    weakIn: [
      {
        where: "Programas e Serviços, Serviços Diversos",
        why: "o ativo contábil é pequeno frente ao valor gerado — o ROA fica artificialmente alto e não compara com nada",
      },
    ],
    caveat:
      "Este ROA divide o lucro dos controladores pelo ativo total consolidado — fatias diferentes nos dois lados. A variante de fatia única é o roa_total (ADR 0026).",
  },
  roa_total: {
    formula: "Lucro líquido consolidado (anualizado) ÷ Ativo total",
    what: "O ROA em fatia única: o lucro do grupo inteiro (minoritários incluídos) sobre o ativo que esse mesmo grupo inteiro opera. Como o ativo total só existe consolidado, esta é a versão internamente consistente do índice.",
    strongIn: [
      {
        where: "Grupos com controladas relevantes não integrais",
        why: "o ativo consolidado inclui 100% das controladas, então o lucro que o divide deve incluir 100% do resultado delas — mesma fatia em cima e embaixo",
      },
    ],
    weakIn: [
      {
        where: "Empresas sem minoritários relevantes",
        why: "é numericamente igual ao roa — a coluna só acrescenta informação onde há participação minoritária material",
      },
    ],
  },
  roic: {
    formula: "EBIT anualizado × (1 − 34%) ÷ (Patrimônio líquido + Dívida líquida)",
    what: "Retorno sobre todo o capital investido no negócio, próprio e de terceiros, medido antes da estrutura de capital. Comparado ao custo de capital, diz se a empresa cria ou destrói valor.",
    strongIn: [
      {
        where: "Máquinas e Equipamentos, Material de Transporte, Químicos",
        why: "isola a qualidade da operação do efeito da alavancagem, permitindo comparar concorrentes com dívidas diferentes",
      },
      {
        where: "Mineração, Siderurgia, Exploração e Refino",
        why: "capital-intensivos por natureza: o teste real é se o retorno supera o custo do capital ao longo do ciclo",
      },
      {
        where: "Energia Elétrica, Água e Saneamento",
        why: "retorno regulado sobre base de ativos — o ROIC é o número que o regulador, na prática, define",
      },
    ],
    weakIn: [
      {
        where: "Todo o setor Financeiro",
        why: "dívida é insumo operacional, não financiamento; separar capital próprio de terceiros não faz sentido",
      },
    ],
    naSectors: ["bank"],
    caveat:
      "O NOPAT usa a alíquota estatutária fixa de 34% (IRPJ 25% + CSLL 9%), não a alíquota efetiva de cada empresa — uma aproximação deliberada, registrada em docs/adr/0002.",
  },
  net_margin: {
    formula: "Lucro líquido ÷ Receita líquida (mesmo período, sem anualizar)",
    what: "Quanto sobra de cada real vendido depois de tudo — custos, despesas, juros e impostos.",
    strongIn: [
      {
        where: "Bebidas, Produtos de Uso Pessoal e de Limpeza, Medicamentos",
        why: "margem alta e estável é a assinatura de poder de marca e de preço",
      },
      {
        where: "Comércio, Alimentos Processados",
        why: "margens estruturalmente finas: um ou dois pontos percentuais separam o líder do resto",
      },
    ],
    weakIn: [
      {
        where: "Comparações entre subsetores diferentes",
        why: "uma margem de 3% no varejo alimentar pode ser excelente e uma de 15% em software, medíocre — a margem só compara dentro do mesmo subsetor",
      },
      {
        where: "Holdings Diversificadas, Exploração de Imóveis",
        why: "resultado dominado por equivalência patrimonial ou reavaliação de ativos, não pela receita",
      },
    ],
    caveat:
      "Esta margem usa o lucro dos controladores sobre a receita consolidada — fatias diferentes. A versão de fatia única, que as plataformas publicam, é a net_margin_total (ADR 0026).",
  },
  net_margin_total: {
    formula: "Lucro líquido consolidado ÷ Receita líquida (mesmo período, sem anualizar)",
    what: "A margem líquida em fatia única: o lucro do grupo inteiro (minoritários incluídos) sobre a receita, que só existe consolidada. É a margem que as plataformas de referência publicam.",
    strongIn: [
      {
        where: "Grupos com controladas relevantes não integrais",
        why: "a receita inclui 100% das vendas das controladas, então o lucro que a divide deve incluir 100% do resultado delas — mesma fatia em cima e embaixo",
      },
    ],
    weakIn: [
      {
        where: "Empresas sem minoritários relevantes",
        why: "é numericamente igual à net_margin — a coluna só acrescenta informação onde há participação minoritária material",
      },
    ],
  },
  gross_margin: {
    formula: "Lucro bruto ÷ Receita líquida",
    what: "O que sobra depois apenas do custo do produto ou serviço vendido. Mede o poder de precificação puro, antes de qualquer despesa de estrutura.",
    strongIn: [
      {
        where: "Programas e Serviços, Medicamentos",
        why: "custo marginal baixo: a margem bruta revela o quanto o produto é insubstituível",
      },
      {
        where: "Tecidos, Vestuário e Calçados, Utilidades Domésticas",
        why: "distingue marca de commodity dentro do mesmo subsetor",
      },
    ],
    weakIn: [
      {
        where: "Bancos e Seguradoras",
        why: "não existe custo do produto vendido na estrutura contábil financeira",
      },
      {
        where: "Construção e Engenharia, Construção Civil",
        why: "o custo é reconhecido por evolução de obra, então a margem bruta oscila com o cronograma, não com a rentabilidade",
      },
    ],
    naSectors: ["insurer"],
  },
  ebit_margin: {
    formula: "EBIT (lucro operacional) ÷ Receita líquida",
    what: "Margem da operação depois das despesas de estrutura, mas antes de juros e impostos. Mostra a rentabilidade do negócio em si, sem o efeito da dívida.",
    strongIn: [
      {
        where: "Bens Industriais em geral, Comércio e Distribuição",
        why: "captura a alavancagem operacional: quanto da receita adicional vira lucro operacional",
      },
      {
        where: "Energia Elétrica, Telecomunicações",
        why: "permite comparar operações com estruturas de dívida muito distintas",
      },
    ],
    weakIn: [
      {
        where: "Todo o setor Financeiro",
        why: "juros são receita e despesa operacionais — excluí-los descaracteriza o resultado",
      },
    ],
    naSectors: ["insurer"],
  },
  ebitda_margin: {
    formula: "EBITDA ÷ Receita líquida",
    what: "Margem operacional antes também de depreciação e amortização — uma aproximação da geração de caixa da operação.",
    strongIn: [
      {
        where: "Energia Elétrica, Água e Saneamento, Telecomunicações",
        why: "a depreciação é enorme e pouco informativa; o EBITDA aproxima o caixa que o ativo produz",
      },
      {
        where: "Transporte, Construção e Engenharia",
        why: "padroniza empresas com políticas de depreciação diferentes",
      },
    ],
    weakIn: [
      {
        where: "Mineração, Siderurgia, Exploração e Refino",
        why: "ignorar depreciação em negócios que precisam reinvestir pesadamente todo ano superestima a geração de caixa real — compare com o FCL",
      },
      {
        where: "Todo o setor Financeiro",
        why: "mesma razão do EBIT: juros são operacionais",
      },
    ],
    naSectors: FINANCIAL,
  },
  asset_turnover: {
    formula: "Receita líquida (anualizada) ÷ Ativo total",
    what: "Quantas vezes o ativo se converte em vendas no ano. É a outra metade do ROA: margem × giro = retorno sobre ativo.",
    strongIn: [
      {
        where: "Comércio, Comércio e Distribuição, Alimentos Processados",
        why: "o modelo é girar estoque rápido com margem fina — o giro é o indicador de eficiência do setor",
      },
    ],
    weakIn: [
      {
        where: "Intermediários Financeiros, Previdência e Seguros",
        why: "'receita sobre ativo' não descreve nada num balanço bancário; o número é calculável mas não interpretável",
      },
      {
        where: "Energia Elétrica, Exploração de Imóveis",
        why: "giro estruturalmente baixo por desenho do negócio — baixo não significa ineficiente",
      },
    ],
  },

  // ---------------------------------------------------------- por ação ---
  eps: {
    formula: "Lucro líquido (anualizado) ÷ Número de ações",
    what: "A fatia do lucro que cabe a cada ação. É o denominador do P/L e a base de comparação da própria empresa ao longo do tempo.",
    strongIn: [
      {
        where: "Qualquer subsetor, na série histórica da própria empresa",
        why: "o LPA crescente ao longo dos anos é o sinal de que o lucro cresce mais rápido do que a diluição",
      },
    ],
    weakIn: [
      {
        where: "Comparação entre empresas diferentes",
        why: "depende inteiramente de quantas ações existem — um LPA de R$ 10 não é melhor que um de R$ 1",
      },
      {
        where: "Construção Civil, Saúde",
        why: "emissões frequentes diluem o LPA mesmo com o lucro total subindo",
      },
    ],
    caveat:
      "Hoje aparece como n/d em todos os tickers: o plano gratuito da brapi devolve o valor de mercado mas não o número de ações, então o denominador falta. Correção em andamento (issue #22).",
  },
  bvps: {
    formula: "Patrimônio líquido ÷ Número de ações",
    what: "O capital contábil que lastreia cada ação. Comparado ao preço, produz o P/VP.",
    strongIn: [
      {
        where: "Intermediários Financeiros, Previdência e Seguros",
        why: "os ativos estão majoritariamente marcados a valor de mercado, então o valor patrimonial se aproxima do valor econômico",
      },
      {
        where: "Exploração de Imóveis, Securitizadoras",
        why: "o balanço é essencialmente uma carteira de ativos avaliáveis",
      },
    ],
    weakIn: [
      {
        where: "Programas e Serviços, Medicamentos, Mídia",
        why: "o que gera valor (marca, software, pesquisa) foi despesado, não capitalizado — o VPA subestima enormemente o negócio",
      },
    ],
    caveat:
      "Como o LPA, hoje aparece como n/d por falta do número de ações na fonte de preços (issue #22). O P/VP, que não depende dele, segue calculado normalmente.",
  },

  // ------------------------------------------------------- crescimento ---
  revenue_growth: {
    formula: "(Receita atual − Receita anterior) ÷ |Receita anterior|",
    what: "Variação da receita frente ao período anterior comparável. Crescimento de receita sem crescimento de lucro costuma indicar compressão de margem.",
    strongIn: [
      {
        where: "Programas e Serviços, Saúde, Comércio",
        why: "em negócios de escala, a receita cresce antes do lucro — é o indicador que antecipa a virada",
      },
    ],
    weakIn: [
      {
        where: "Mineração, Siderurgia, Exploração e Refino, Agropecuária",
        why: "a receita segue o preço da commodity, não a execução da empresa: um crescimento de 40% pode ser só o minério subindo",
      },
      {
        where: "Empresas com base pequena ou receita anterior próxima de zero",
        why: "o percentual fica enorme e sem significado",
      },
    ],
  },
  net_income_growth: {
    formula: "(Lucro atual − Lucro anterior) ÷ |Lucro anterior|",
    what: "Variação do lucro líquido frente ao período anterior comparável.",
    strongIn: [
      {
        where: "Intermediários Financeiros, Energia Elétrica",
        why: "lucro recorrente e previsível: o crescimento reflete de fato a operação",
      },
    ],
    weakIn: [
      {
        where: "Qualquer subsetor saindo de prejuízo",
        why: "o divisor negativo torna o percentual irreconhecível — Smaug usa o módulo do valor anterior, mas o número segue difícil de ler",
      },
      {
        where: "Holdings Diversificadas, Exploração e Refino",
        why: "eventos não recorrentes (venda de ativos, hedge, impairment) dominam a variação",
      },
    ],
    caveat:
      "Divide pelo valor absoluto do lucro anterior, de modo que a saída de um prejuízo aparece como crescimento positivo em vez de sinal invertido.",
  },

  // -------------------------------------------- alavancagem & liquidez ---
  net_debt: {
    formula: "Dívida total − Caixa e aplicações financeiras",
    what: "A dívida que sobraria se a empresa usasse todo o seu caixa para quitá-la. Valor negativo significa caixa líquido.",
    strongIn: [
      {
        where: "Energia Elétrica, Telecomunicações, Construção e Engenharia",
        why: "endividamento é parte estrutural do modelo; a dívida líquida é o que de fato precisa ser servido",
      },
    ],
    weakIn: [
      {
        where: "Todo o setor Financeiro",
        why: "captação é o insumo do negócio, não um passivo a ser quitado — a conta não tem sentido econômico",
      },
    ],
    naSectors: ["bank"],
  },
  net_debt_to_ebitda: {
    formula: "Dívida líquida ÷ EBITDA anualizado",
    what: "Quantos anos de geração operacional seriam necessários para zerar a dívida líquida. É a métrica que os próprios covenants bancários usam.",
    strongIn: [
      {
        where: "Energia Elétrica, Água e Saneamento, Telecomunicações",
        why: "fluxo de caixa previsível torna o múltiplo diretamente comparável entre pares",
      },
      {
        where: "Construção e Engenharia, Transporte",
        why: "é o indicador que antecipa quebra de covenant e emissão diluidora",
      },
    ],
    weakIn: [
      {
        where: "Mineração, Siderurgia, Exploração e Refino",
        why: "no topo do ciclo o EBITDA infla e o múltiplo parece confortável — meça contra o EBITDA médio do ciclo, não o do ano",
      },
      {
        where: "Todo o setor Financeiro",
        why: "não há dívida líquida a medir",
      },
    ],
    naSectors: ["bank"],
  },
  debt_to_equity: {
    formula: "Dívida total ÷ Patrimônio líquido",
    what: "Quanto de capital de terceiros a empresa usa para cada real de capital próprio. Alavancagem financeira em sua forma mais crua.",
    strongIn: [
      {
        where: "Bens Industriais, Materiais Básicos, Construção Civil",
        why: "mede a folga de balanço para atravessar uma queda de demanda",
      },
    ],
    weakIn: [
      {
        where: "Todo o setor Financeiro",
        why: "a alavancagem é o modelo de negócio e é limitada por regra prudencial, não por prudência do gestor — compare índice de Basileia, não D/PL",
      },
      {
        where: "Empresas com PL negativo",
        why: "o índice muda de sinal e deixa de ser ordenável",
      },
    ],
    naSectors: ["bank"],
  },
  liabilities_to_assets: {
    formula: "(Ativo total − Patrimônio líquido) ÷ Ativo total",
    what: "Fatia dos ativos financiada por terceiros. É o indicador de alavancagem que sobrevive a qualquer estrutura de balanço.",
    strongIn: [
      {
        where: "Intermediários Financeiros, Previdência e Seguros",
        why: "é o único indicador de alavancagem desta seção que continua calculável e legível num balanço financeiro",
      },
      {
        where: "Comparações entre subsetores muito diferentes",
        why: "não depende de a empresa ter 'dívida' no sentido clássico",
      },
    ],
    weakIn: [
      {
        where: "Comparar um banco com uma indústria",
        why: "um banco opera perto de 90% e é saudável; uma indústria em 90% está à beira do abismo. O número só compara dentro do subsetor",
      },
    ],
  },
  current_ratio: {
    formula: "Ativo circulante ÷ Passivo circulante",
    what: "Capacidade de honrar as obrigações dos próximos doze meses com os recursos que se convertem em caixa no mesmo prazo.",
    strongIn: [
      {
        where: "Comércio, Tecidos, Vestuário e Calçados, Máquinas e Equipamentos",
        why: "estoque e recebíveis dominam o circulante: o índice mede folga real de curto prazo",
      },
    ],
    weakIn: [
      {
        where: "Todo o setor Financeiro",
        why: "o balanço não é segregado em circulante e não circulante da forma clássica",
      },
      {
        where: "Energia Elétrica, Comércio e Distribuição",
        why: "operam de propósito com liquidez corrente abaixo de 1, financiados por fornecedores e por receita previsível — não é sinal de aperto",
      },
    ],
    naSectors: ["bank"],
  },

  // ------------------------------------------------ múltiplos de mercado ---
  pe: {
    formula: "Valor de mercado ÷ Lucro líquido anualizado",
    what: "Quantos anos de lucro atual o mercado está pagando pela empresa. O múltiplo mais usado e o mais fácil de usar errado.",
    strongIn: [
      {
        where: "Intermediários Financeiros, Previdência e Seguros, Energia Elétrica",
        why: "lucro recorrente e pouco cíclico: o P/L de hoje é uma boa proxy do de amanhã",
      },
    ],
    weakIn: [
      {
        where: "Mineração, Siderurgia, Exploração e Refino",
        why: "a armadilha clássica do ciclo: no pico dos preços o lucro é máximo e o P/L parece baratíssimo, justamente quando o risco é maior",
      },
      {
        where: "Qualquer empresa com prejuízo",
        why: "o múltiplo fica negativo e perde sentido — Smaug o exibe assim mesmo, sem escondê-lo",
      },
    ],
  },
  pb: {
    formula: "Valor de mercado ÷ Patrimônio líquido",
    what: "Quanto o mercado paga por cada real de capital contábil. Faz par com o ROE: um P/VP alto só se justifica por um ROE alto e durável.",
    strongIn: [
      {
        where: "Intermediários Financeiros, Previdência e Seguros, Serviços Financeiros Diversos",
        why: "o patrimônio é composto de ativos avaliáveis, então o valor contábil ancora o valor econômico",
      },
      {
        where: "Exploração de Imóveis, Securitizadoras",
        why: "o balanço é a própria carteira de ativos",
      },
    ],
    weakIn: [
      {
        where: "Programas e Serviços, Medicamentos, Mídia",
        why: "o ativo que gera caixa (software, marca, patente) foi despesado e não está no PL — o P/VP parece absurdamente alto sem que a ação esteja cara",
      },
      {
        where: "Empresas com PL negativo",
        why: "o múltiplo inverte de sinal e deixa de ser comparável",
      },
    ],
  },
  psr: {
    formula: "Valor de mercado ÷ Receita líquida anualizada",
    what: "Quanto o mercado paga por real de receita. Útil exatamente onde o lucro ainda não existe ou não é representativo.",
    strongIn: [
      {
        where: "Programas e Serviços, Saúde, Comércio",
        why: "empresas em fase de escala, com lucro pequeno ou negativo, mas receita já significativa",
      },
    ],
    weakIn: [
      {
        where: "Intermediários Financeiros, Previdência e Seguros",
        why: "'receita' num banco (juros brutos) ou numa seguradora (prêmios) não é comparável à receita de uma indústria",
      },
      {
        where: "Comércio e Distribuição, Alimentos Processados",
        why: "receita gigante com margem de 1–3%: um PSR baixo é a norma, não uma pechincha",
      },
    ],
  },
  price_to_assets: {
    formula: "Valor de mercado ÷ Ativo total",
    what: "Quanto o mercado paga por real de ativo, sem descontar o passivo. Um primo grosseiro do P/VP.",
    strongIn: [
      {
        where: "Intermediários Financeiros, Securitizadoras, Exploração de Imóveis",
        why: "o ativo é a carteira que gera o resultado, e está razoavelmente marcado a mercado",
      },
    ],
    weakIn: [
      {
        where: "Programas e Serviços, Serviços Diversos",
        why: "ativo contábil irrelevante frente ao valor gerado — o múltiplo dispara sem informar",
      },
      {
        where: "Comparações entre empresas com alavancagens diferentes",
        why: "ignora completamente quem é dono desses ativos; prefira o P/VP ou o EV/EBITDA",
      },
    ],
  },
  price_to_ebit: {
    formula: "Valor de mercado ÷ EBIT anualizado",
    what: "P/L calculado antes de juros e impostos. Isola o preço pago pela operação, sem o efeito do regime tributário nem da dívida no numerador do lucro.",
    strongIn: [
      {
        where: "Bens Industriais, Consumo Cíclico, Materiais Básicos",
        why: "compara operações com carga tributária ou benefícios fiscais distintos",
      },
    ],
    weakIn: [
      {
        where: "Todo o setor Financeiro",
        why: "não há EBIT com significado numa estrutura em que juros são operacionais",
      },
      {
        where: "Empresas muito endividadas",
        why: "o numerador ignora a dívida mas o denominador a exclui do custo — para essas, use EV/EBITDA",
      },
    ],
  },
  price_to_working_capital: {
    formula: "Valor de mercado ÷ (Ativo circulante − Passivo circulante)",
    what: "Múltiplo sobre o capital de giro líquido, na tradição de Graham: procura empresas negociadas perto (ou abaixo) do seu capital circulante.",
    strongIn: [
      {
        where: "Comércio, Tecidos, Vestuário e Calçados, Máquinas e Equipamentos",
        why: "capital de giro positivo e substancial, composto de estoque e recebíveis conversíveis",
      },
    ],
    weakIn: [
      {
        where: "Energia Elétrica, Água e Saneamento, Comércio e Distribuição",
        why: "capital de giro estruturalmente negativo torna o múltiplo negativo e sem interpretação",
      },
      {
        where: "Todo o setor Financeiro",
        why: "não há circulante clássico a subtrair",
      },
    ],
    naSectors: ["bank"],
  },
  payout: {
    formula: "Proventos pagos no exercício ÷ Lucro líquido",
    what: "Fatia do lucro distribuída aos acionistas. O complemento (1 − payout) é o que ficou retido para crescer.",
    strongIn: [
      {
        where: "Energia Elétrica, Água e Saneamento, Telecomunicações",
        why: "negócios maduros e regulados: um payout alto e estável é a tese de investimento",
      },
      {
        where: "Intermediários Financeiros, Previdência e Seguros",
        why: "distribuição limitada por capital regulatório — o payout revela a folga prudencial",
      },
    ],
    weakIn: [
      {
        where: "Programas e Serviços, Saúde, Construção Civil",
        why: "payout baixo é escolha estratégica de reinvestimento, não fraqueza",
      },
      {
        where: "Qualquer empresa em ano de lucro deprimido",
        why: "manter o dividendo com lucro menor produz payout acima de 100%, o que sinaliza distribuição de reservas, não geração do ano",
      },
    ],
    caveat:
      "O numerador vem dos proventos efetivamente pagos no fluxo de caixa do exercício, que podem se referir ao resultado do ano anterior. Em anos de mudança de política, descasa do payout 'declarado'.",
  },
  dividend_yield: {
    formula: "Proventos pagos no exercício ÷ Valor de mercado",
    what: "Retorno em caixa que os proventos do período representam sobre o preço da ação.",
    strongIn: [
      {
        where: "Energia Elétrica, Água e Saneamento, Previdência e Seguros",
        why: "distribuição regular e previsível: o yield é a parcela dominante do retorno total",
      },
      {
        where: "Intermediários Financeiros",
        why: "juros sobre capital próprio tornam a distribuição estruturalmente alta e recorrente",
      },
    ],
    weakIn: [
      {
        where: "Mineração, Exploração e Refino",
        why: "dividendos extraordinários no topo do ciclo produzem um yield de dois dígitos que não se repete",
      },
      {
        where: "Empresas que acabaram de cair de preço",
        why: "o yield sobe porque o denominador caiu — é sintoma, não atrativo",
      },
    ],
    caveat:
      "No histórico de anos fechados o denominador é o preço médio ajustado por proventos daquele exercício; nos últimos 12 meses é o preço nominal atual. Ver docs/adr/0001.",
  },
  ev_ebitda: {
    formula: "(Valor de mercado + Dívida líquida) ÷ EBITDA anualizado",
    what: "Quanto custa a empresa inteira — sócios e credores — por real de geração operacional. É o múltiplo neutro à estrutura de capital.",
    strongIn: [
      {
        where: "Energia Elétrica, Telecomunicações, Transporte",
        why: "compara diretamente empresas com endividamentos muito diferentes, o que o P/L não faz",
      },
      {
        where: "Mineração, Siderurgia, Químicos",
        why: "é o múltiplo que o mercado de fusões e aquisições usa nesses subsetores",
      },
    ],
    weakIn: [
      {
        where: "Todo o setor Financeiro",
        why: "dívida líquida não existe conceitualmente, então o valor da firma não é construível",
      },
      {
        where: "Subsetores de capex pesado e recorrente",
        why: "o EBITDA ignora o reinvestimento obrigatório; um EV/EBITDA baixo pode esconder um FCL nulo",
      },
    ],
    naSectors: ["bank"],
  },

  // -------------------------------------------------------- fluxo de caixa ---
  fcf: {
    formula: "(Caixa operacional − CAPEX), anualizado",
    what: "O caixa que sobra depois de manter o negócio de pé. É o que efetivamente pode virar dividendo, recompra ou amortização de dívida.",
    strongIn: [
      {
        where: "Energia Elétrica, Telecomunicações, Mineração",
        why: "expõe o que o EBITDA esconde: quanto do lucro operacional volta para o ativo em vez de ir para o acionista",
      },
      {
        where: "Máquinas e Equipamentos, Consumo não Cíclico",
        why: "converte a qualidade do lucro contábil em caixa verificável",
      },
    ],
    weakIn: [
      {
        where: "Intermediários Financeiros, Previdência e Seguros",
        why: "o caixa operacional é dominado pela variação da carteira de crédito e das provisões técnicas — o número existe, mas não é 'caixa livre' em sentido algum",
      },
      {
        where: "Construção Civil, Agropecuária",
        why: "ciclos longos de estoque fazem o FCL de um único ano oscilar violentamente; leia a série, não o ponto",
      },
    ],
    caveat:
      "Smaug não distingue CAPEX de manutenção de CAPEX de expansão — um ano de investimento pesado em crescimento aparece como FCL deprimido. Além disso, alguns exercícios fechados antigos vêm como n/d porque a linha de CAPEX muda de rótulo no arquivo da CVM (issue #22).",
  },
  price_to_fcf: {
    formula: "Valor de mercado ÷ Fluxo de caixa livre anualizado",
    what: "Quantos anos de caixa livre o mercado está pagando. O P/L do caixa, imune a artifícios contábeis.",
    strongIn: [
      {
        where: "Energia Elétrica, Telecomunicações, Consumo não Cíclico",
        why: "FCL estável e positivo torna o múltiplo diretamente comparável entre pares",
      },
    ],
    weakIn: [
      {
        where: "Empresas em ciclo de investimento",
        why: "FCL próximo de zero ou negativo faz o múltiplo explodir ou inverter — não significa que a ação esteja cara",
      },
      {
        where: "Todo o setor Financeiro",
        why: "o FCL subjacente não representa geração livre de caixa",
      },
    ],
  },
  fcf_yield: {
    formula: "Fluxo de caixa livre anualizado ÷ Valor de mercado",
    what: "O inverso do P/FCL: o retorno em caixa que a empresa gera sobre o preço pago por ela. Comparável direto com a taxa livre de risco.",
    strongIn: [
      {
        where: "Energia Elétrica, Água e Saneamento, Telecomunicações",
        why: "confrontar o FCF yield com a NTN-B é a forma mais limpa de julgar o preço desses ativos",
      },
      {
        where: "Comparado ao dividend yield, em qualquer subsetor",
        why: "se o dividend yield supera o FCF yield de forma persistente, a distribuição está vindo de dívida ou de caixa acumulado",
      },
    ],
    weakIn: [
      {
        where: "Empresas com FCL negativo",
        why: "yield negativo não é ordenável junto com os positivos",
      },
      {
        where: "Todo o setor Financeiro",
        why: "mesma ressalva do FCL",
      },
    ],
  },

  // ---------------------------------------------------- valores absolutos ---
  revenue: {
    formula: "Receita líquida do período (sem anualizar)",
    what: "O topo da demonstração de resultado: quanto a empresa vendeu, líquido de devoluções e impostos sobre venda.",
    strongIn: [
      {
        where: "Qualquer subsetor, na série histórica",
        why: "a trajetória da receita é o sinal mais difícil de manipular contabilmente",
      },
    ],
    weakIn: [
      {
        where: "Comparação de tamanho entre subsetores",
        why: "uma distribuidora fatura múltiplos de uma empresa de software muito mais valiosa",
      },
    ],
  },
  net_income: {
    formula: "Lucro líquido atribuído aos controladores",
    what: "O resultado final do exercício, já descontada a parcela dos acionistas minoritários das controladas. É a fatia que pareia com o LPA; o total consolidado é o net_income_total (ADR 0026).",
    strongIn: [
      {
        where: "Intermediários Financeiros, Energia Elétrica",
        why: "lucro recorrente: a série anual descreve bem a capacidade de geração",
      },
    ],
    weakIn: [
      {
        where: "Holdings Diversificadas, Exploração e Refino",
        why: "impairments, hedge e venda de ativos podem dominar o resultado de um ano isolado",
      },
    ],
  },
  net_income_total: {
    formula: "Lucro líquido consolidado do período (DRE 3.11, como arquivado)",
    what: "O resultado do grupo inteiro, incluindo a parcela dos acionistas minoritários das controladas. É o numerador das variantes _total (ADR 0026); em grupos integrais coincide com o net_income.",
    strongIn: [
      {
        where: "Grupos com controladas relevantes não integrais",
        why: "mostra quanto o conglomerado gerou como um todo, antes de repartir com os sócios minoritários das controladas",
      },
    ],
    weakIn: [
      {
        where: "Empresas sem minoritários relevantes",
        why: "é numericamente igual ao net_income — a coluna só acrescenta informação onde há participação minoritária material",
      },
    ],
  },
  dividends: {
    formula: "Proventos pagos no exercício (fluxo de caixa de financiamento)",
    what: "O caixa que efetivamente saiu da empresa para os acionistas no período, somando dividendos e juros sobre capital próprio.",
    strongIn: [
      {
        where: "Energia Elétrica, Água e Saneamento, Previdência e Seguros",
        why: "a série de proventos é a tese central desses subsetores",
      },
    ],
    weakIn: [
      {
        where: "Qualquer subsetor, num ano isolado",
        why: "o pagamento se refere com frequência ao resultado do exercício anterior, então o valor descasa do lucro exibido ao lado",
      },
    ],
  },

  // ------------------------------------------------------------ escala ---
  // Figuras de tamanho (reais absolutos / contagem), exibidas no topo da página,
  // não no grid de índices.
  market_cap: {
    formula: "Σ (preço da classe × ações da classe em circulação)",
    what: "Quanto o mercado paga pela empresa inteira: a soma sobre cada classe listada, cada uma ao seu próprio preço, contando só as ações em circulação (líquidas de tesouraria).",
    strongIn: [
      {
        where: "Qualquer subsetor",
        why: "é o denominador de todos os múltiplos de mercado — o preço que o mercado atribui ao negócio todo",
      },
    ],
    weakIn: [
      {
        where: "Empresas com uma única cotação líquida",
        why: "no plano grátis do brapi só algumas ações retornam preço, então o valor pode ficar nulo",
      },
    ],
  },
  enterprise_value: {
    formula: "Valor de mercado + dívida líquida",
    what: "O que custaria comprar a empresa e quitar suas dívidas: o valor de mercado mais a dívida líquida de caixa. É o preço do negócio independente de como ele é financiado.",
    strongIn: [
      {
        where: "Indústria, Energia, Saneamento",
        why: "compara empresas com estruturas de capital diferentes numa base única",
      },
    ],
    weakIn: [
      {
        where: "Holdings com muito caixa",
        why: "a dívida líquida negativa (caixa maior que a dívida) encolhe o EV abaixo do valor de mercado",
      },
    ],
    naSectors: ["bank"],
    caveat:
      "Nulo para um banco: depósito é funding, não dívida, então não há dívida líquida a somar (ADR 0022).",
  },
  shares: {
    formula: "Ações emitidas − ações em tesouraria",
    what: "Quantas ações estão de fato em circulação — o denominador do LPA e do VPA. Exclui as ações que a própria empresa recomprou e mantém em tesouraria.",
    strongIn: [
      {
        where: "Qualquer subsetor",
        why: "a base de ações define quanto do lucro e do patrimônio cabe a cada ação",
      },
    ],
    weakIn: [
      {
        where: "Units (SAPR11, TAEE11)",
        why: "uma unit é um pacote de ações de classes diferentes, então a contagem por classe não vira um número por unit direto (#38)",
      },
    ],
  },

  // -------------------------------------------------------------- banco ---
  // Só um banco preenche estes três: o balanço dele é o negócio (ADR 0021).
  net_interest_margin: {
    formula: "Margem financeira bruta (spread antes da provisão) ÷ Ativo total",
    what: "O quanto o banco ganha de spread — juros que recebe menos juros que paga — sobre cada real de ativo. É o preço do dinheiro dele, antes de descontar os calotes.",
    strongIn: [
      {
        where: "Intermediários Financeiros (bancos)",
        why: "é a receita primária do negócio: emprestar caro e captar barato",
      },
    ],
    weakIn: [
      {
        where: "Bancos com forte receita de serviços",
        why: "parte relevante do lucro vem de tarifas e seguros, que a margem financeira não enxerga — olhe o índice de eficiência ao lado",
      },
    ],
    naSectors: ["insurer", "utility", "commodity", "industry"],
    caveat:
      "Dividimos pelo ativo total, não pelos ativos rentáveis: o CVM não separa uns dos outros na demonstração estruturada. Isso subestima a margem em relação ao número que o próprio banco publica.",
  },
  efficiency_ratio: {
    formula:
      "(Despesas com pessoal + administrativas) ÷ (Margem financeira bruta + Tarifas)",
    what: "Quanto do que o banco ganha é consumido pela própria estrutura — agências, pessoal, back office. Aqui, menor é melhor: 33% significa que um terço da receita vira custo interno.",
    strongIn: [
      {
        where: "Intermediários Financeiros (bancos)",
        why: "é o placar de gestão do setor, e o que separa um banco caro de um enxuto",
      },
    ],
    weakIn: [
      {
        where: "Comparações entre bancos de perfis diferentes",
        why: "um banco de varejo carrega milhares de agências e nunca terá a eficiência de um banco de atacado — o índice compara mal fora do mesmo perfil",
      },
    ],
    naSectors: ["insurer", "utility", "commodity", "industry"],
    caveat:
      "Os bancos publicam um índice gerencial, com ajustes próprios. O nosso sai direto da demonstração, então costuma ficar alguns pontos acima do divulgado.",
  },
  cost_of_risk: {
    formula: "Provisão para créditos duvidosos (do ano) ÷ Carteira de crédito",
    what: "Quanto o banco precisou reservar para calotes, em relação a tudo que emprestou. É o preço do risco que ele escolheu correr — e sobe antes do lucro cair.",
    strongIn: [
      {
        where: "Intermediários Financeiros (bancos)",
        why: "é o indicador que antecipa a deterioração: a provisão sobe no balanço antes de o calote aparecer no lucro",
      },
    ],
    weakIn: [
      {
        where: "Um ano isolado",
        why: "a provisão responde a mudanças de política de crédito e a eventos setoriais — só a série de vários anos mostra a tendência",
      },
    ],
    naSectors: ["insurer", "utility", "commodity", "industry"],
  },
};

export function indicatorDoc(key: IndicatorKey): IndicatorDoc {
  return INDICATOR_DOCS[key];
}
