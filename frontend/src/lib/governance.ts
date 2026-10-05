const LISTING_SEGMENTS: Record<string, string> = {
  novo_mercado: "Novo Mercado",
  nivel_1: "Nível 1",
  nivel_2: "Nível 2",
  basico: "Básico",
  mercado_soma: "Mercado SOMA",
  balcao_nao_organizado: "Balcão não organizado",
  companhia_menor_porte: "Companhia de menor porte",
  bovespa_mais: "Bovespa Mais",
  bovespa_mais_nivel_2: "Bovespa Mais Nível 2",
};

export function listingSegmentLabel(segment: string | null | undefined): string {
  return segment ? LISTING_SEGMENTS[segment] ?? "Não identificado" : "Não identificado";
}
