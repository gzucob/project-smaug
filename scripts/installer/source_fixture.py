"""CI-only public-source responses; run real preparation against disposable DBs."""

import csv
import io
import re
import zipfile

import httpx

from smaug.entrypoints.cli import main

CNPJ = "33.000.167/0001-01"


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("latin-1")


def archive(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in members.items():
            zipped.writestr(name, content)
    return output.getvalue()


def fca(year: int) -> bytes:
    return archive(
        {
            f"fca_cia_aberta_geral_{year}.csv": csv_bytes(
                [
                    {
                        "CNPJ_Companhia": CNPJ,
                        "Versao": "1",
                        "Nome_Empresarial": "PETROBRAS",
                        "Codigo_CVM": "9512",
                        "Situacao_Registro_CVM": "Ativo",
                        "Setor_Atividade": "Petróleo e Gás",
                    }
                ]
            ),
            f"fca_cia_aberta_valor_mobiliario_{year}.csv": csv_bytes(
                [
                    {
                        "CNPJ_Companhia": CNPJ,
                        "Versao": "1",
                        "Codigo_Negociacao": "PETR4",
                        "Mercado": "Bolsa",
                        "Sigla_Entidade_Administradora": "B3",
                        "Data_Fim_Negociacao": "",
                        "Data_Inicio_Listagem": "2024-01-02",
                        "Valor_Mobiliario": "Ações Preferenciais",
                        "Composicao_BDR_Unit": "",
                    }
                ]
            ),
        }
    )


def statements(dataset: str, year: int) -> bytes:
    references = (
        (f"{year}-12-31",)
        if dataset == "dfp"
        else tuple(f"{year}-{quarter}" for quarter in ("03-31", "06-30", "09-30"))
    )
    reference = references[-1]
    accounts = {
        "BPA": {"1": "2000", "1.01": "1000", "1.01.01": "100"},
        "BPP": {"2": "2000", "2.01": "500", "2.02": "500", "2.03": "1000"},
        "DRE": {
            "3.01": "1000",
            "3.02": "-500",
            "3.03": "500",
            "3.04": "-200",
            "3.05": "300",
            "3.06": "-50",
            "3.07": "250",
            "3.08": "-50",
            "3.09": "200",
            "3.11": "200",
        },
        "DFC_MI": {"6.01": "200", "6.02": "-50", "6.03": "-50"},
        "DVA": {"7.04.01": "20"},
    }
    members: dict[str, bytes] = {}
    for module, values in accounts.items():
        members[f"{dataset}_cia_aberta_{module}_con_{year}.csv"] = csv_bytes(
            [
                {
                    "CNPJ_CIA": CNPJ,
                    "DT_REFER": reference,
                    "VERSAO": "1",
                    "DENOM_CIA": "PETROBRAS",
                    "CD_CVM": "9512",
                    "GRUPO_DFP": "DF Consolidado",
                    "MOEDA": "REAL",
                    "ESCALA_MOEDA": "MIL",
                    "ORDEM_EXERC": "ÚLTIMO",
                    "DT_INI_EXERC": f"{year}-01-01",
                    "DT_FIM_EXERC": reference,
                    "CD_CONTA": account,
                    "DS_CONTA": account,
                    "VL_CONTA": value,
                    "ST_CONTA_FIXA": "S",
                }
                for reference in references
                for account, value in values.items()
            ]
        )
    members[f"{dataset}_cia_aberta_composicao_capital_{year}.csv"] = csv_bytes(
        [
            {
                "CNPJ_CIA": CNPJ,
                "DT_REFER": reference,
                "VERSAO": "1",
                "DENOM_CIA": "PETROBRAS",
                "QT_ACAO_ORDIN_CAP_INTEGR": "0",
                "QT_ACAO_PREF_CAP_INTEGR": "1000000",
                "QT_ACAO_TOTAL_CAP_INTEGR": "1000000",
                "QT_ACAO_ORDIN_TESOURO": "0",
                "QT_ACAO_PREF_TESOURO": "0",
                "QT_ACAO_TOTAL_TESOURO": "0",
            }
        ]
    )
    return archive(members)


def fre(year: int) -> bytes:
    members = {
        f"fre_cia_aberta_capital_social_{year}.csv": csv_bytes(
            [
                {
                    "CNPJ_Companhia": CNPJ,
                    "Data_Referencia": f"{year}-12-31",
                    "Versao": "1",
                    "ID_Documento": "1",
                    "Nome_Companhia": "PETROBRAS",
                    "ID_Capital_Social": "1",
                    "Tipo_Capital": "Capital Integralizado",
                    "Data_Autorizacao_Aprovacao": "2024-01-01",
                    "Valor_Capital": "1000000",
                    "Prazo_Integralizacao": "",
                    "Quantidade_Acoes_Ordinarias": "0",
                    "Quantidade_Acoes_Preferenciais": "1000000",
                    "Quantidade_Total_Acoes": "1000000",
                }
            ]
        )
    }

    members[f"fre_cia_aberta_capital_social_classe_acao_{year}.csv"] = csv_bytes(
        [
            {
                "CNPJ_Companhia": CNPJ,
                "Data_Referencia": f"{year}-12-31",
                "Versao": "1",
                "Nome_Companhia": "PETROBRAS",
                "ID_Capital_Social": "1",
                "Tipo_Classe_Acao_Preferencial": "PN",
                "Quantidade_Acoes": "1000000",
            }
        ]
    )
    members[f"fre_cia_aberta_posicao_acionaria_{year}.csv"] = csv_bytes(
        [
            {
                "CNPJ_Companhia": CNPJ,
                "Nome_Companhia": "PETROBRAS",
                "Data_Referencia": f"{year}-12-31",
                "Versao": "1",
                "ID_Documento": "1",
                "ID_Acionista": "1",
                "Acionista": "Acionista Teste",
                "Tipo_Pessoa_Acionista": "PJ",
                "ID_Acionista_Relacionado": "",
                "Acionista_Relacionado": "",
                "Tipo_Pessoa_Acionista_Relacionado": "",
                "Quantidade_Acao_Ordinaria_Circulacao": "0",
                "Percentual_Acao_Ordinaria_Circulacao": "0",
                "Quantidade_Acao_Preferencial_Circulacao": "100000",
                "Percentual_Acao_Preferencial_Circulacao": "10",
                "Quantidade_Total_Acoes_Circulacao": "100000",
                "Percentual_Total_Acoes_Circulacao": "10",
                "Acionista_Controlador": "Não",
                "Data_Ultima_Alteracao": f"{year}-01-01",
            }
        ]
    )
    return archive(members)


def cotahist(year: int) -> bytes:
    records = []
    for day in (f"{year}0102", f"{year}0630"):
        row = list(" " * 245)
        for start, text in (
            (0, "01"),
            (2, day),
            (10, "02"),
            (12, "PETR4       "),
            (24, "010"),
            (56, "0000000001000"),
            (108, "0000000001000"),
            (210, "0000001"),
            (230, "BRPETRACNPR6"),
        ):
            row[start : start + len(text)] = text
        records.append("".join(row))
    return archive({f"COTAHIST_A{year}.TXT": ("\n".join(records) + "\n").encode()})


def source(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/DADOS/"):
        dataset = path.split("/")[-3].lower()
        years = (2024, 2025) if dataset == "dfp" else (2025, 2026)
        return httpx.Response(
            200,
            text="".join(
                f'<a href="{dataset}_cia_aberta_{year}.zip">file</a>' for year in years
            ),
        )
    if match := re.search(r"(fca|dfp|itr|fre)_cia_aberta_(\d{4})\.zip$", path):
        dataset, year = match[1], int(match[2])
        content = (
            fca(year)
            if dataset == "fca"
            else (fre(year) if dataset == "fre" else statements(dataset, year))
        )
        return httpx.Response(200, content=content)
    if match := re.search(r"COTAHIST_A(\d{4})\.ZIP$", path):
        return httpx.Response(200, content=cotahist(int(match[1])))
    if "GetDownloadIndustryClassification" in path:
        values = [
            "",
            "Petróleo, Gás e Biocombustíveis",
            "Petróleo, Gás e Biocombustíveis",
            "Exploração, Refino e Distribuição",
            "PETROBRAS",
            "PETR",
        ]
        cells = "".join(
            f'<c r="{chr(65 + i)}1" t="inlineStr"><is><t>{v}</t></is></c>'
            for i, v in enumerate(values)
        )
        xml = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData><row r="1">{cells}</row></sheetData></worksheet>'
        )
        return httpx.Response(
            200, content=archive({"xl/worksheets/sheet1.xml": xml.encode()})
        )
    if "GetDetail" in path:
        return httpx.Response(
            200,
            json={
                "issuingCompany": "PETR",
                "codeCVM": "9512",
                "cnpj": CNPJ,
                "dateQuotation": "02/01/2024",
            },
        )
    if "GetListedSupplementCompany" in path:
        return httpx.Response(
            200,
            json={
                "code": "PETR",
                "codeCVM": "9512",
                "cnpj": CNPJ,
                "tradingName": "PETROBRAS",
                "stockDividends": [],
                "subscriptions": [],
            },
        )
    if "GetListedCashDividends" in path:
        return httpx.Response(
            200, json={"page": {"pageNumber": 1, "totalPages": 1}, "results": []}
        )
    raise RuntimeError(f"Unexpected source request: {request.url}")


class FixtureClient(httpx.AsyncClient):
    def __init__(self, **kwargs: object) -> None:
        kwargs["transport"] = httpx.MockTransport(source)
        super().__init__(**kwargs)  # type: ignore[arg-type]


if __name__ == "__main__":
    httpx.AsyncClient = FixtureClient
    main()
