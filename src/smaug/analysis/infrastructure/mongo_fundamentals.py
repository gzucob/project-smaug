"""Maps the raw CVM mirror (Mongo) into standardized financials.

This is the derivation bridge: it reads the append-only ``raw_ingestions`` docs
that the CVM ingestion stored (source="cvm"), groups them by reference period,
and pulls the specific accounts each indicator needs — by CVM code where the
code is stable across sectors, and by (accent-folded) name where the code
differs (equity is 2.03 for a normal company but 2.07 for a bank).

The mapping keys on the **accounting regime the filer actually files under**, not
on its ``Sector`` (ADR 0015). The two are not the same, and reading the sector is
how accounts that exist go unread: BBSE3 is an insurer that files a corporate-
shaped balance sheet, and CXSE3 is an insurer that files as a holding outright.

The same CVM code also means different things per regime, so a code is only ever
read within its regime's branch (ADR 0005's dead needle, twice over): ``3.05`` is
EBIT for a corporate filer but *pre-tax profit* for a bank (whose EBIT is at no
code at all — interest is its operation), and ``2.01.04`` is "Empréstimos e
Financiamentos" for a corporate filer but "Capitalização" for an insurer.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from smaug.analysis.domain.financials import (
    AccountingRegime,
    StandardizedFinancials,
    expected_regime,
)
from smaug.portfolio.domain.sectors import Sector, sector_of

_STATEMENTS = ("BPA", "BPP", "DRE", "DFC", "DMPL")

# The mirror stores every filing and chooses none of them (ADR 0016), so the
# choice is made here: the reported period rather than its comparative, the latest
# amendment, the consolidated statement over the parent-only one, and — of an ITR's
# two income-statement columns — the one accumulated from 01-Jan rather than the
# isolated quarter (see ``_rank``, #83).
#
# A filing that predates ADR 0016 carries neither discriminator; it is treated as
# the reported period at version 0, so an old mirror still reads correctly.
_CURRENT_PERIOD = "ULTIMO"  # vs. PENULTIMO, the prior-period comparative column
_BALANCE_RANK: dict[str, int] = {"consolidated": 1, "individual": 0}

# D&A is the one line we still deliberately skip for a financial filer: a bank
# files it inside a filer-specific "Outras Despesas Operacionais" breakdown whose
# sub-codes are not stable across banks, and no indicator consumes it (EBITDA is
# inapplicable under both financial regimes — ADR 0010). Naming it here keeps the
# null honest if a future indicator does reach for it (#27).
_FINANCIAL_UNMAPPED_FIELDS = frozenset({"dep_amort", "ebitda"})

# How the DRE's opening line (3.01) reads under each accounting regime,
# accent-folded. Verified against the real filings in the raw mirror: banks
# open with "Receitas de Intermediação Financeira", insurers with "Receitas das
# Atividades Seguradoras/Resseguradoras", and the corporate schema with
# "Receita de Venda de Bens e/ou Serviços" — which is how CXSE3, an insurer by
# sector, actually files (as a holding; ADR 0006).
_REGIME_MARKERS: tuple[tuple[str, AccountingRegime], ...] = (
    ("intermediacao financeira", AccountingRegime.BANK),
    ("seguradora", AccountingRegime.INSURANCE),
    ("receita de venda", AccountingRegime.CORPORATE),
)

# Closed-year (historical) view: keep only annual periods. In Brazil the annual
# DFP closes on 31-Dec, while the ITRs are Q1–Q3 (never December), so the month
# alone distinguishes a closed year without depending on a per-filing document tag.
_CLOSED_YEAR_MONTH = 12

# The DRE's bottom line, in priority order (the label varies by sector, and by
# whether the statement is consolidated or parent-only). Used as the total whose
# controllers' share is read — see ``_net_income``.
#
# The parent-only line matters for a bank, whose income statement we take from the
# parent filing (ADR 0019). It has to outrank the "operações continuadas" fallbacks:
# those sit *above* the profit-sharing deduction, and BBAS3's parent 2024 reads
# R$39.8 bn there against R$35.3 bn at the bottom line — the R$4.5 bn its employees
# are paid.
_NET_INCOME_TOTAL_NAMES = (
    "lucro ou prejuizo liquido consolidado do periodo",
    "lucro/prejuizo consolidado do periodo",
    "lucro ou prejuizo liquido do periodo",
    "resultado liquido das operacoes continuadas",
    "lucro ou prejuizo das operacoes continuadas",
)

Accounts = Sequence[Mapping[str, Any]]


class RawCollection(Protocol):
    """Minimal read surface over the ``raw_ingestions`` collection."""

    def find(self, filter: Mapping[str, Any], /) -> Any: ...


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _by_code(accounts: Accounts, code: str) -> Decimal | None:
    for account in accounts:
        if str(account.get("code")) == code:
            return _dec(account.get("quantity"))
    return None


def _account_by_name(accounts: Accounts, needle: str) -> Mapping[str, Any] | None:
    folded = _fold(needle)
    for account in accounts:
        if folded in _fold(str(account.get("name", ""))):
            return account
    return None


def _by_name(accounts: Accounts, needle: str) -> Decimal | None:
    account = _account_by_name(accounts, needle)
    return None if account is None else _dec(account.get("quantity"))


def _child_by_name(accounts: Accounts, parent: str, needle: str) -> Decimal | None:
    """The first line matching ``needle`` among ``parent``'s sub-accounts.

    Scoping the search to the parent's own children is what keeps the DRE's *two*
    "Atribuído aos Sócios..." blocks apart. A bank files the pair twice — once
    under 3.09 (Resultado das Operações Continuadas) and once under 3.11 (Lucro
    Consolidado) — and leaves the 3.09 pair blank; an unscoped name search reads
    that leading zero and reports the bank as earning nothing (BBAS3's Q3, #78).
    """
    prefix = f"{parent}."
    folded = _fold(needle)
    for account in accounts:
        if not str(account.get("code", "")).startswith(prefix):
            continue
        if folded in _fold(str(account.get("name", ""))):
            return _dec(account.get("quantity"))
    return None


def _direct_child_by_name(
    accounts: Accounts, parent: str, needle: str
) -> Decimal | None:
    """Like ``_child_by_name``, but only ``parent``'s *direct* children match.

    The controllers'/minority pair is always filed one level under its total
    (3.11.01/3.11.02, 2.03.09, a bank's 2.07.02), while a deeper descendant can
    carry the same words and mean something else entirely: TOTS3's capital
    reserves file ``2.03.02.09 — Prêmio na Compra de Participação de Não
    Controladores`` *before* the real minority block 2.03.09, and the prefix
    match read that reserve as the minority interest — reporting a controllers'
    equity larger than the consolidated total (#118). The bank lines keep the
    descendant-scoped ``_child_by_name``: their needles deliberately read at
    depths that are not stable across filers (ADR 0021).
    """
    prefix = f"{parent}."
    depth = parent.count(".") + 1
    folded = _fold(needle)
    for account in accounts:
        code = str(account.get("code", ""))
        if not code.startswith(prefix) or code.count(".") != depth:
            continue
        if folded in _fold(str(account.get("name", ""))):
            return _dec(account.get("quantity"))
    return None


def _controllers_share(
    accounts: Accounts,
    *,
    total: Mapping[str, Any] | None,
    controllers: str,
    minority: str,
) -> Decimal | None:
    """The controlling shareholders' slice of a consolidated figure.

    Platforms report the controllers' equity/earnings, not the consolidated total
    that still carries the minority interest. The split is exposed in two shapes:
    an explicit "attributed to the controller" sub-line (banks), or a total plus a
    "non-controlling" sub-line (most companies). Both are read as direct children
    of the total (``_direct_child_by_name``): prefer the explicit line, else fall
    back on the
    accounting identity ``controllers = total − minority``, which an absent
    minority line reduces to the total — the no-split-filed case.

    An explicit **zero** is the exception. Where the identity yields a non-zero
    figure, a zero on the controllers' line is an unfilled field, not an economic
    zero, and the identity wins: CXSE3 files 3.11.01 = 0 against a 3.7bn total and
    a zero minority (#78), which read literally reports a profitable insurer as
    earning nothing. A controllers' share that is *genuinely* zero requires the
    minority to take the whole total — and there the identity yields zero too, so
    it still reads zero. A non-zero explicit line is always believed, so a real
    bank split (BBAS3's 3.11.01) keeps winning over the total.
    """
    if total is None:
        return None
    total_value = _dec(total.get("quantity"))
    parent = str(total.get("code", ""))
    explicit = _direct_child_by_name(accounts, parent, controllers)
    minority_value = _direct_child_by_name(accounts, parent, minority)

    derived: Decimal | None = None
    if total_value is not None:
        derived = total_value - (
            minority_value if minority_value is not None else Decimal(0)
        )

    unfilled_zero = explicit == 0 and derived is not None and derived != 0
    if explicit is not None and not unfilled_zero:
        return explicit
    return derived


def _net_income_total_account(dre: Accounts) -> Mapping[str, Any] | None:
    """The DRE's bottom-line account — the consolidated total, minority included."""
    for name in _NET_INCOME_TOTAL_NAMES:
        total = _account_by_name(dre, name)
        if total is not None:
            return total
    return None


def _net_income(dre: Accounts) -> Decimal | None:
    """Net income attributable to the controlling shareholders (DRE)."""
    return _controllers_share(
        dre,
        total=_net_income_total_account(dre),
        controllers="socios da empresa controladora",
        minority="socios nao controladores",
    )


def _net_income_total(dre: Accounts) -> Decimal | None:
    """The consolidated bottom line as filed (ADR 0026's total basis)."""
    total = _net_income_total_account(dre)
    return None if total is None else _dec(total.get("quantity"))


def _equity(bpp: Accounts) -> Decimal | None:
    """Equity attributable to the controlling shareholders (BPP)."""
    return _controllers_share(
        bpp,
        total=_account_by_name(bpp, "patrimonio liquido"),
        controllers="atribuido ao controlador",
        minority="nao controladores",
    )


def _equity_total(bpp: Accounts) -> Decimal | None:
    """The consolidated equity as filed, minority block included (ADR 0026)."""
    return _by_name(bpp, "patrimonio liquido")


def _dividends_paid(dfc: Accounts) -> Decimal | None:
    """Dividends + interest-on-equity (JCP) paid to controlling shareholders.

    Financing-section cash outflows whose label mentions a dividend or JCP
    (``capital proprio``) and "pago", excluding the non-controlling line.
    Returned positive (the DFC records them as negative outflows); ``None`` when
    no such line exists, so DY degrades to null rather than zero.
    """
    total = Decimal(0)
    found = False
    for account in dfc:
        name = _fold(str(account.get("name", "")))
        if "pag" not in name or "nao control" in name:
            continue
        if "dividendo" not in name and "capital proprio" not in name:
            continue
        value = _dec(account.get("quantity"))
        if value is not None:
            total += abs(value)
            found = True
    return total if found else None


# The D&A add-back's folded-name needles. "deprecia" covers the singular and the
# plural in one prefix — LREN3, VIVT3 and SAPR11 file "Depreciações e
# amortizações", which a singular-substring search read as absent (#114).
# "amortizac" (never the looser "amortiza") keeps the financial lines out:
# "ativos financeiros ao custo amortizado" sits in a 6.01 too. "exaust" is
# depletion, which KLBN11 files as its own line — Dados de Mercado's EBITDA
# leaves that line out (its KLBN11 2024 margin is 33.7% vs our 43.1%), but
# depletion is the D of a pulp company's DD&A and Klabin's own reported 2024
# EBITDA margin (~41%) sides with including it.
_DEP_AMORT_NEEDLES = ("deprecia", "amortizac", "exaust")


def _dep_amort(dfc: Accounts) -> Decimal | None:
    """Depreciation, amortization and depletion — the DFC's operating add-backs.

    Summed over the operating section (6.01.*) rather than read off one line,
    because the charge is not always one line: HAPV3 and TAEE11 file the
    right-of-use depreciation as a sibling of the main D&A line, and HAPV3's
    2025 DFP has no combined line at all — only "Amortização de direito de uso"
    plus "Depreciação de direito de uso". The 6.01 scope is what keeps the
    financing section's "Amortização de empréstimos" (a debt repayment, not a
    charge) out of EBITDA. A line nested under an already-summed one is skipped
    so a parent and its breakdown are never double-counted.
    """
    total = Decimal(0)
    found = False
    summed: list[str] = []
    for account in dfc:
        code = str(account.get("code", ""))
        if not code.startswith("6.01"):
            continue
        if any(code.startswith(f"{parent}.") for parent in summed):
            continue
        name = _fold(str(account.get("name", "")))
        if not any(needle in name for needle in _DEP_AMORT_NEEDLES):
            continue
        value = _dec(account.get("quantity"))
        if value is None:
            continue
        total += value
        found = True
        summed.append(code)
    return total if found else None


# The DMPL rows that carry a dividend/JCP declaration, inside 5.04 ("Transações
# de Capital com os Sócios"). Matched by folded name; the 5.04 scope keeps the
# treasury rows (5.04.04/05) and the reserve destinations (5.06.*) out, and the
# negative-sign filter keeps "dividendos prescritos" (a *return* to equity,
# positive) from netting the declaration down.
_DECLARED_PREFIX = "5.04"
_DECLARED_NEEDLES = ("dividendo", "juros sobre capital", "capital proprio")


def _dividends_declared(dmpl: Accounts) -> Decimal | None:
    """Dividends + JCP declared against equity in the period (DMPL 5.04 rows).

    The DMPL is a matrix — each account repeats once per equity column — and the
    column *names* cannot be trusted: BBDC4's filing shifts them (its R$166bn
    controllers' equity sits under "Participação dos Não Controladores", and the
    consolidated total under an unnamed column). So the row's figure is read
    structurally: a declaration posts one-signed cells, and the largest absolute
    cell is the row's total column. Read from the parent-only statement wherever
    it exists (see ``_load``) — the parent's declaration is what the listed
    shareholders receive, and the parent DMPL has no minority column to shift.

    Returned positive. ``None`` only when the DMPL itself is absent; a filed
    DMPL with no declaration row is an economic **zero** — the company declared
    nothing in the period — not a missing input, and reading it as null would
    void every TTM window containing one quiet quarter. Note the basis is
    *declared during the period*: a dividend the AGM approves months after
    year-end lands in the next year's DMPL, so a filer that declares mostly
    after closing (rather than as intra-year JCP) still shows the gap the
    platforms' "of the exercise" attribution closes by hand.
    """
    if not dmpl:
        return None
    rows: dict[tuple[str, str], Decimal] = {}
    for account in dmpl:
        code = str(account.get("code", ""))
        if not code.startswith(_DECLARED_PREFIX):
            continue
        name = _fold(str(account.get("name", "")))
        if not any(needle in name for needle in _DECLARED_NEEDLES):
            continue
        value = _dec(account.get("quantity"))
        if value is None or value >= 0:
            continue
        key = (code, name)
        if key not in rows or abs(value) > abs(rows[key]):
            rows[key] = value
    return sum((abs(value) for value in rows.values()), Decimal(0))


def _capex(dfc: Accounts) -> Decimal | None:
    """Cash spent on PP&E and intangibles (DFC investing section, 6.02.*).

    Sums the outflows (negative amounts) whose label mentions ``imobilizado`` or
    ``intangivel``. Disposals (positive inflows, e.g. "alienação de imobilizado")
    are ignored — this is gross capex, the cash-out leg of free cash flow.
    Returned positive; ``None`` when no such line exists, so FCF degrades to null.
    """
    total = Decimal(0)
    found = False
    for account in dfc:
        if not str(account.get("code", "")).startswith("6.02"):
            continue
        name = _fold(str(account.get("name", "")))
        if "imob" not in name and "intangiv" not in name:
            continue
        value = _dec(account.get("quantity"))
        if value is not None and value < 0:
            total += -value
            found = True
    return total if found else None


def _sum(*values: Decimal | None) -> Decimal | None:
    total = Decimal(0)
    present = False
    for value in values:
        if value is not None:
            total += value
            present = True
    return total if present else None


def _iso_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _period_start(by_module: Mapping[str, Any], module: str) -> date | None:
    """Start of a statement's flow period (its ``period_start_date``)."""
    payload = by_module.get(module)
    if isinstance(payload, Mapping):
        return _iso_date(payload.get("period_start_date"))
    return None


def _accounts(by_module: Mapping[str, Any], module: str) -> Accounts:
    payload = by_module.get(module)
    if not isinstance(payload, Mapping):
        return []
    accounts = payload.get("accounts")
    return accounts if isinstance(accounts, list) else []


def _scale(by_module: Mapping[str, Any], module: str) -> Decimal:
    """CVM figures are reported in ``currency_size`` units (usually thousands).

    Scaling to absolute reais here is what keeps the market multiples honest —
    brapi's market cap is in reais, so mixing the two unscaled inflates P/E,
    P/B and EV/EBITDA by ~1000x.
    """
    payload = by_module.get(module)
    if isinstance(payload, Mapping):
        size = payload.get("currency_size")
        if isinstance(size, int) and size > 0:
            return Decimal(size)
    return Decimal(1)


def _mul(value: Decimal | None, scale: Decimal) -> Decimal | None:
    return None if value is None else value * scale


def _filed_regime(dre: Accounts) -> AccountingRegime | None:
    """The regime this filer actually reports under, read off the DRE's 3.01 label.

    ``None`` when the DRE is absent or its opening line matches no known
    schema — an unknown regime is never guessed, so a mismatch (#30's
    "unexpected regime" cause) is only ever flagged from positive evidence.
    """
    for account in dre:
        if str(account.get("code")) == "3.01":
            label = _fold(str(account.get("name", "")))
            for marker, regime in _REGIME_MARKERS:
                if marker in label:
                    return regime
            return None
    return None


def _bank_dre(
    chosen: Mapping[str, Any] | None, parent: Mapping[str, Any] | None
) -> Mapping[str, Any] | None:
    """The parent-only income statement, when the filer is a bank (ADR 0019).

    A bank files two income statements that **materially disagree**, and only one of
    them is the result anybody quotes. BBAS3's 2024 DFP: the parent statement closes
    at R$35.3 bn, the consolidated one at R$29.2 bn — R$26.4 bn of it attributed to
    the controllers. The bank reports 35.3, the press reports 35.3, and the reference
    platforms' LPA divides 35.3 (BBDC4: 19.1 bn against the consolidated 17.3 bn).
    Nobody, anywhere, publishes the consolidated figure: the two statements are drawn
    under different accounting standards, and the market reads the BACEN one.

    The **balance sheet** stays consolidated — there the market reads the other one
    (Bradesco's published total assets are the consolidated R$2.07 tn, not the
    parent's R$1.69 tn). The asymmetry is the filings', not ours.

    Returns ``None`` when the filer is not a bank, or when it filed no parent income
    statement, leaving the ordinary choice (the consolidated one) in place.
    """
    if chosen is None or parent is None:
        return None
    if _filed_regime(_accounts_of(chosen)) is not AccountingRegime.BANK:
        return None
    return parent


def _accounts_of(payload: Mapping[str, Any]) -> Accounts:
    accounts = payload.get("accounts")
    return accounts if isinstance(accounts, Sequence) else ()


def standardize(
    by_module: Mapping[str, Any], sector: Sector, reference_date: date
) -> StandardizedFinancials:
    """Build one period's ``StandardizedFinancials`` from its CVM statements.

    Dispatches on the regime the filer actually filed under, falling back to the
    one its sector predicts when the DRE is missing or its opening line matches
    no known schema — mapping under a guessed regime would read the wrong codes.
    """
    bpa, bpa_s = _accounts(by_module, "BPA"), _scale(by_module, "BPA")
    bpp, bpp_s = _accounts(by_module, "BPP"), _scale(by_module, "BPP")
    dre, dre_s = _accounts(by_module, "DRE"), _scale(by_module, "DRE")
    dfc, dfc_s = _accounts(by_module, "DFC"), _scale(by_module, "DFC")
    dmpl, dmpl_s = _accounts(by_module, "DMPL"), _scale(by_module, "DMPL")

    filed_regime = _filed_regime(dre)

    # Lines that sit at the same code under every regime.
    base = StandardizedFinancials(
        reference_date=reference_date,
        sector=sector,
        period_start=_period_start(by_module, "DRE"),
        dfc_period_start=_period_start(by_module, "DFC"),
        total_assets=_mul(_by_code(bpa, "1"), bpa_s),
        equity=_mul(_equity(bpp), bpp_s),
        net_income=_mul(_net_income(dre), dre_s),
        # Both slices travel together (ADR 0026). For a bank the DRE here is the
        # parent filing (ADR 0019), so its "total" is the parent bottom line —
        # the figure the bank itself reports.
        equity_total=_mul(_equity_total(bpp), bpp_s),
        net_income_total=_mul(_net_income_total(dre), dre_s),
        revenue=_mul(_by_code(dre, "3.01"), dre_s),
        gross_profit=_mul(_by_code(dre, "3.03"), dre_s),
        dividends_paid=_mul(_dividends_paid(dfc), dfc_s),
        dividends_declared=_mul(_dividends_declared(dmpl), dmpl_s),
        dmpl_period_start=_period_start(by_module, "DMPL"),
        cfo=_mul(_by_code(dfc, "6.01"), dfc_s),  # net operating cash flow
        capex=_mul(_capex(dfc), dfc_s),
        filed_regime=filed_regime,
    )

    regime = filed_regime or expected_regime(sector)
    if regime is AccountingRegime.BANK:
        return _as_bank(base, bpa, bpa_s, dre, dre_s)
    if regime is AccountingRegime.INSURANCE:
        return _as_insurer(base, bpa, bpa_s, bpp, bpp_s, dre, dre_s)
    return _as_corporate(base, bpa, bpa_s, bpp, bpp_s, dre, dre_s, dfc, dfc_s)


def _as_bank(
    base: StandardizedFinancials,
    bpa: Accounts,
    bpa_s: Decimal,
    dre: Accounts,
    dre_s: Decimal,
) -> StandardizedFinancials:
    """A bank's balance sheet has no current/non-current split and no debt line.

    ``gross_profit`` carries 3.03, which for a bank is the net interest income —
    the spread, its closest analogue to a gross result. ``ebit`` carries 3.05,
    which for a bank is profit *before tax*, not before interest: interest is the
    operation, so there is no line to strip. Both are deliberate approximations,
    matching what the reference platforms compute (ADR 0015); ``total_debt``,
    ``current_assets`` and ``current_liabilities`` stay ``None`` because the
    schema has no such lines — the calculator names those nulls inapplicable.

    Everything below is read **by label, scoped to its parent** rather than by code,
    because the two banks do not agree on the codes: the loan-loss provision is
    3.02.05 for BBAS3 and 3.02.04 for BBDC4 (#27). The provision sits *inside* 3.02
    here — the parent filing's chart of accounts (ADR 0019) deducts it before the
    3.03 spread — which is why ``gross_profit`` for a bank is net of it, and why the
    calculator adds it back to get the interest margin.

    Índice de Basileia (capital adequacy) is deliberately **not** built here (issue
    #102, ANL-33) — its inputs are regulatory, not accounting. The numerator is the
    Patrimônio de Referência (Nível I + Nível II under BACEN Res. 4.192, with
    prudential adjustments) and the denominator is the RWA (Ativos Ponderados pelo
    Risco); neither is a CVM statement account. A probe of the raw mirror
    (2026-07-17) found both banks file exactly BPA/BPP/DRE/DFC/DMPL/DRA/DVA + FRE
    capital: the BPP equity block (2.07.*) is the ordinary Patrimônio Líquido, not
    the Patrimônio de Referência; the only "capital principal" strings are the
    accounting footprint of one hybrid instrument (its interest/redemption lines in
    the DFC/DMPL); "ponderado pelo risco"/RWA appears nowhere. The ratio lives only
    in the bank's Pillar 3 / gerenciamento-de-capital notes, which the mirror does
    not ingest (ADR 0016), so it is left unpublished rather than added as a
    permanently-null column.
    """
    return replace(
        base,
        ebit=_mul(_by_code(dre, "3.05"), dre_s),  # pre-tax result — see docstring
        cash=_mul(_by_code(bpa, "1.01"), bpa_s),  # no 1.01.01/1.01.02 split
        loan_loss_provision=_mul(_child_by_name(dre, "3.02", "provisao"), dre_s),
        fee_income=_mul(_child_by_name(dre, "3.04", "prestacao de servicos"), dre_s),
        personnel_expense=_mul(_child_by_name(dre, "3.04", "pessoal"), dre_s),
        admin_expense=_mul(_child_by_name(dre, "3.04", "administrativas"), dre_s),
        loan_book=_loan_book(bpa, bpa_s),
        unmapped_fields=_FINANCIAL_UNMAPPED_FIELDS,
    )


def _loan_book(bpa: Accounts, scale: Decimal) -> Decimal | None:
    """The credit portfolio, net of the provision carried against it.

    Both banks file the loan book under "Ativos Financeiros ao Custo Amortizado"
    (1.02.04) with the balance-sheet provision as its sibling — but only BBDC4
    fills that provision line in (BBAS3 files zero there, its portfolio already net).
    Subtracting it where it is filed puts the two banks on one basis: what the bank
    still expects to collect.
    """
    gross = _child_by_name(bpa, "1.02.04", "operacoes de credito")
    if gross is None:
        return None
    provision = _child_by_name(bpa, "1.02.04", "provisao") or Decimal(0)
    return _mul(gross + provision, scale)  # the provision is filed negative


def _as_insurer(
    base: StandardizedFinancials,
    bpa: Accounts,
    bpa_s: Decimal,
    bpp: Accounts,
    bpp_s: Decimal,
    dre: Accounts,
    dre_s: Decimal,
) -> StandardizedFinancials:
    """An insurer files a corporate-shaped balance sheet but its own DRE.

    So the current/non-current split *is* there (1.01 / 2.01), while EBIT sits at
    3.07 rather than 3.05 — the insurer DRE carries an extra level. ``total_debt``
    stays ``None`` on purpose: the insurer schema has no borrowings line at all
    (2.01.04 is "Capitalização" here, and 2.02.01 is payables and provisions), so
    there is nothing to read (ADR 0015).
    """
    return replace(
        base,
        ebit=_mul(_by_code(dre, "3.07"), dre_s),  # before financial result/taxes
        cash=_mul(_sum(_by_code(bpa, "1.01.01"), _by_code(bpa, "1.01.02")), bpa_s),
        current_assets=_mul(_by_code(bpa, "1.01"), bpa_s),
        current_liabilities=_mul(_by_code(bpp, "2.01"), bpp_s),
        earned_premium=_mul(_by_code(dre, "3.01.01"), dre_s),
        claims_incurred=_mul(_by_code(dre, "3.02.01"), dre_s),
        unmapped_fields=_FINANCIAL_UNMAPPED_FIELDS,
    )


def _as_corporate(
    base: StandardizedFinancials,
    bpa: Accounts,
    bpa_s: Decimal,
    bpp: Accounts,
    bpp_s: Decimal,
    dre: Accounts,
    dre_s: Decimal,
    dfc: Accounts,
    dfc_s: Decimal,
) -> StandardizedFinancials:
    """The standard chart of accounts — and what CXSE3 files, despite its sector."""
    ebit = _mul(_by_code(dre, "3.05"), dre_s)  # before financial result/taxes
    dep_amort = _mul(_dep_amort(dfc), dfc_s)  # cash-flow add-backs, summed
    ebitda = (
        _sum(ebit, dep_amort) if ebit is not None and dep_amort is not None else None
    )
    # Cash for net debt = cash & equivalents (1.01.01) + short-term financial
    # investments (1.01.02), matching how the platforms measure liquidity.
    cash = _mul(_sum(_by_code(bpa, "1.01.01"), _by_code(bpa, "1.01.02")), bpa_s)
    return replace(
        base,
        ebit=ebit,
        ebitda=ebitda,
        dep_amort=dep_amort,
        cash=cash,
        current_assets=_mul(_by_code(bpa, "1.01"), bpa_s),
        current_liabilities=_mul(_by_code(bpp, "2.01"), bpp_s),
        total_debt=_mul(
            _sum(_by_code(bpp, "2.01.04"), _by_code(bpp, "2.02.01")), bpp_s
        ),
    )


def _is_annual(doc_type: str | None, financials: StandardizedFinancials) -> bool:
    """A closed year: the DFP document, or (lacking the tag) a December period."""
    if doc_type is not None:
        return doc_type.upper() == "DFP"
    return financials.reference_date.month == _CLOSED_YEAR_MONTH


class MongoFundamentalsReader:
    """Reads the CVM mirror: ITR quarters (history) and the annual DFP (annual)."""

    def __init__(
        self,
        collection: RawCollection,
        *,
        sector_resolver: Callable[[str], Sector] = sector_of,
    ) -> None:
        self._collection = collection
        # The sector only seeds the ``expected_regime`` fallback (the filed regime,
        # read off the statement, decides applicability). Curated for the nine;
        # the CLI injects a registry-backed resolver for on-demand tickers.
        self._sector_resolver = sector_resolver

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        """ITR quarterly periods (oldest→newest) — the raw material for the TTM."""
        return [f for dt, f in await self._load(ticker) if not _is_annual(dt, f)]

    async def annuals(self, ticker: str) -> list[StandardizedFinancials]:
        """Annual DFPs (closed years), oldest→newest."""
        return [f for dt, f in await self._load(ticker) if _is_annual(dt, f)]

    async def annual(self, ticker: str) -> StandardizedFinancials | None:
        """The most recent annual DFP (closed year), for the Q4 derivation."""
        annuals = await self.annuals(ticker)
        return annuals[-1] if annuals else None

    async def _load(
        self, ticker: str
    ) -> list[tuple[str | None, StandardizedFinancials]]:
        cursor = self._collection.find({"source": "cvm", "ticker": ticker})
        docs: list[Mapping[str, Any]] = await cursor.to_list(None)
        sector = self._sector_resolver(ticker)

        by_period: dict[str, dict[str, Any]] = {}
        doc_type: dict[str, str | None] = {}
        best: dict[tuple[str, str], tuple[int, int, int, datetime]] = {}
        # The parent-only income statements, kept aside: for a bank they are the
        # ones that carry the result the bank itself reports (see ``_bank_dre``).
        parent_dre: dict[str, Mapping[str, Any]] = {}
        parent_best: dict[str, tuple[int, int, int, datetime]] = {}
        # The parent-only DMPL, preferred for EVERY filer: the parent's declared
        # dividends are what the listed shareholders receive, and the parent
        # statement has no minority column for a shifted header to hide (#104).
        parent_dmpl: dict[str, Mapping[str, Any]] = {}
        parent_dmpl_best: dict[str, tuple[int, int, int, datetime]] = {}
        for doc in docs:
            payload = doc.get("payload")
            module = doc.get("module")
            fetched = doc.get("fetched_at")
            if (
                not isinstance(payload, Mapping)
                or module not in _STATEMENTS
                or fetched is None
            ):
                continue
            ref = payload.get("reference_date")
            if not isinstance(ref, str):
                continue
            if _ordem(payload) != _CURRENT_PERIOD:
                continue  # the comparative describes the prior period, not this one
            key = (ref, module)
            rank = _rank(payload, fetched)
            if key not in best or rank > best[key]:
                best[key] = rank
                by_period.setdefault(ref, {})[module] = payload
                tag = payload.get("document_type")
                if isinstance(tag, str):
                    doc_type[ref] = tag
            if module == "DRE" and payload.get("balance_type") == "individual":
                if ref not in parent_best or rank > parent_best[ref]:
                    parent_best[ref] = rank
                    parent_dre[ref] = payload
            if module == "DMPL" and payload.get("balance_type") == "individual":
                if ref not in parent_dmpl_best or rank > parent_dmpl_best[ref]:
                    parent_dmpl_best[ref] = rank
                    parent_dmpl[ref] = payload

        for ref, modules in by_period.items():
            bank_dre = _bank_dre(modules.get("DRE"), parent_dre.get(ref))
            if bank_dre is not None:
                modules["DRE"] = bank_dre
            if ref in parent_dmpl:
                modules["DMPL"] = parent_dmpl[ref]

        return [
            (doc_type.get(ref), standardize(modules, sector, date.fromisoformat(ref)))
            for ref, modules in sorted(by_period.items())
        ]


def _ordem(payload: Mapping[str, Any]) -> str:
    """Which column of the filing this is — the reported period, or its comparative."""
    ordem = payload.get("ordem_exerc")
    return ordem if isinstance(ordem, str) else _CURRENT_PERIOD


def _span_months(payload: Mapping[str, Any]) -> int:
    """Months covered by this filing's period column (0 for a balance sheet)."""
    start = _iso_date(payload.get("period_start_date"))
    end = _iso_date(payload.get("reference_date"))
    if start is None or end is None:
        return 0
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def _rank(
    payload: Mapping[str, Any], fetched: datetime
) -> tuple[int, int, int, datetime]:
    """How strongly one filing is preferred over another for the same period+module.

    Version dominates the balance type: the amendment supersedes the original even
    when only the parent-only statement was refiled. ``fetched_at`` is the last
    resort — two ingestions of the identical filing, the newer copy wins.

    The span breaks the remaining tie, and it is the choice #83 exists to make: an
    ITR files its income statement in two columns — accumulated from 01-Jan and the
    isolated quarter — which are otherwise identical filings. The **accumulated**
    (longest) column is taken, for two reasons: ``build_ttm`` already isolates a
    quarter from its span (``YTDₙ − YTDₙ₋₁``), and the DFC offers no other column, so
    this keeps the DRE and the DFC on one period basis. A filer that files only the
    isolated column still ranks first — there is nothing longer to lose to — and its
    3-month span tells ``build_ttm`` it is already isolated.
    """
    version = payload.get("version")
    balance = payload.get("balance_type")
    return (
        version if isinstance(version, int) else 0,
        _BALANCE_RANK.get(balance if isinstance(balance, str) else "", 0),
        _span_months(payload),
        fetched,
    )
