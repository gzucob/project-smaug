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

import re
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
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.infrastructure.mirror import mirror_filter, no_registrant
from smaug.portfolio.domain.company import RegistrantResolver
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.share_classes import PerShareClass, UnitComponent

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
_BANK_UNMAPPED_FIELDS = _FINANCIAL_UNMAPPED_FIELDS | frozenset(
    {"current_financial_investments"}
)

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
# The last entry is the pre-2020 bank chart's bottom line (#155). It is last
# because it is the least specific name of the set, and it must never outrank a
# modern one; the banks that file it stopped doing so after 2019. Note that the
# *code* moved too — the old chart's bottom line is 3.13, and its 3.11 is
# "Reversão dos Juros sobre Capital Próprio", filed as zero — which is why the
# bottom line is found by name here and never by code.
_NET_INCOME_TOTAL_NAMES = (
    "lucro ou prejuizo liquido consolidado do periodo",
    "lucro/prejuizo consolidado do periodo",
    "lucro ou prejuizo liquido do periodo",
    "resultado liquido das operacoes continuadas",
    "lucro ou prejuizo das operacoes continuadas",
    "lucro/prejuizo do periodo",
)

Accounts = Sequence[Mapping[str, Any]]
PerShareResolver = Callable[[str], tuple[UnitComponent, ...]]


class RawCollection(Protocol):
    """Minimal read surface over the ``raw_ingestions`` collection."""

    def find(self, filter: Mapping[str, Any], /) -> Any: ...


def _no_per_share_components(_ticker: str) -> tuple[UnitComponent, ...]:
    return ()


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
    depths that are not stable across filers (ADR 0015/0058).
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
# depletion, which KLBN11 files as its own line. Depletion is the D of a pulp
# company's DD&A, so the filed line belongs in the add-back just like depreciation
# and amortization.
_DEP_AMORT_NEEDLES = ("deprecia", "amortizac", "exaust")

_PARENTHETICAL = re.compile(r"\([^)]*\)")


def _without_parentheticals(name: str) -> str:
    """A label with its bracketed qualifiers removed.

    A needle that survives this is what the line *is*; one that only appears
    inside the brackets is part of a list of what some other line contains
    (#160). CXSE3 files ``Outros ajustes (Depreciação/Tributos Retidos)`` — an
    "other adjustments" line whose bracket happens to name depreciation, mixed
    with withheld taxes and impossible to separate. Reading it as D&A published
    an EBITDA built on R$1.0 million of miscellany.

    Of the 39 distinct D&A labels in the mirror, CXSE3's two variants are the
    only ones whose needle lives in a bracket; every other filer leads with the
    word. A label like "Depreciação (nota 12)" is untouched — the needle is
    outside.
    """
    return _PARENTHETICAL.sub(" ", name)


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
        # Bracketed qualifiers are stripped first: a needle that only survives
        # inside them describes what some *other* line contains (#160).
        name = _fold(_without_parentheticals(str(account.get("name", ""))))
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

    **A line filed as zero is a value, not an absence** (#159). BBSE3 files
    "Aquisição de imobilizado" at 0 — it is a holding of insurers and owns almost
    no PP&E — and reading that as a missing account took free cash flow, P/FCF and
    the FCF yield to null for six years, in which the answer is simply that it
    invested nothing.

    Hence ``<= 0`` rather than a plain label match: a *positive* line must still
    leave the account unfound. A filer that publishes only "alienação de
    imobilizado" has not told us its acquisitions were zero — it has told us
    nothing about them, and answering 0 there would invent a fact.
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
        if value is not None and value <= 0:
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
    a quote is in reais, so mixing the two unscaled inflates P/E, P/B and
    EV/EBITDA by ~1000x.
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


def _accounts_of(payload: Mapping[str, Any] | None) -> Accounts:
    if payload is None:
        return ()
    accounts = payload.get("accounts")
    return accounts if isinstance(accounts, Sequence) else ()


_PER_SHARE_LABELS: dict[str, PerShareClass] = {
    "on": PerShareClass.ORDINARY,
    "ordinaria": PerShareClass.ORDINARY,
    "ordinarias": PerShareClass.ORDINARY,
    "pn": PerShareClass.PREFERRED,
    "preferencial": PerShareClass.PREFERRED,
    "preferenciais": PerShareClass.PREFERRED,
    "pna": PerShareClass.PREFERRED_A,
    "pnb": PerShareClass.PREFERRED_B,
}


def _filed_per_share(
    dre: Accounts,
    prefix: str,
    components: Sequence[UnitComponent],
) -> tuple[Decimal | None, NullReason | None]:
    """One filed CPC 41 result for a share class or composed unit.

    CVM does not keep class labels in stable code positions: ``.01`` can be ON,
    PN or PNA. The label is therefore authoritative and duplicate conflicting
    values are rejected. Values are already reais per action and deliberately
    bypass the DRE's thousand-real scale.
    """
    if not components:
        return None, NullReason.MISSING_ECONOMIC_RIGHTS

    values: dict[PerShareClass, set[Decimal]] = {}
    expected_level = prefix.count(".") + 1
    for account in dre:
        code = str(account.get("code", ""))
        if not code.startswith(f"{prefix}.") or code.count(".") != expected_level:
            continue
        per_share_class = _PER_SHARE_LABELS.get(_fold(str(account.get("name", ""))))
        value = _dec(account.get("quantity"))
        if per_share_class is not None and value is not None:
            values.setdefault(per_share_class, set()).add(value)

    if not values:
        return None, NullReason.MISSING_CPC41_DISCLOSURE

    total = Decimal(0)
    for component in components:
        candidates = values.get(component.per_share_class, set())
        if len(candidates) != 1:
            return None, NullReason.MISSING_ECONOMIC_RIGHTS
        total += Decimal(component.quantity) * next(iter(candidates))
    return total, None


def standardize(
    by_module: Mapping[str, Any],
    sector: Sector,
    reference_date: date,
    *,
    per_share_components: Sequence[UnitComponent] = (),
    per_share_accounts: Accounts | None = None,
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
    cpc41 = dre if per_share_accounts is None else per_share_accounts
    eps_basic, eps_basic_reason = _filed_per_share(
        cpc41, "3.99.01", per_share_components
    )
    eps_diluted, eps_diluted_reason = _filed_per_share(
        cpc41, "3.99.02", per_share_components
    )

    # Lines that sit at the same code under every regime.
    base = StandardizedFinancials(
        reference_date=reference_date,
        sector=sector,
        period_start=_period_start(by_module, "DRE"),
        dfc_period_start=_period_start(by_module, "DFC"),
        total_assets=_mul(_by_code(bpa, "1"), bpa_s),
        equity=_mul(_equity(bpp), bpp_s),
        net_income=_mul(_net_income(dre), dre_s),
        eps_basic=eps_basic,
        eps_diluted=eps_diluted,
        eps_basic_null_reason=eps_basic_reason,
        eps_diluted_null_reason=eps_diluted_reason,
        # Both slices travel together (ADR 0026). The chosen DRE is consolidated
        # whenever the issuer files one, including banks (ADR 0054), so the
        # controller slice and CPC 41 disclosure reconcile on one lineage.
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

    ``gross_profit`` carries the bank's filed 3.03 intermediation result.
    ``ebit`` deliberately stays null: 3.05 is profit before tax, not earnings
    before interest and tax, and exposing it under an EBIT label was a category
    error (ADR 0058). ``total_debt``,
    ``current_assets`` and ``current_liabilities`` stay ``None`` because the
    schema has no such lines — the calculator names those nulls inapplicable.

    Everything below is read **by label, scoped to its parent** rather than by code,
    because the two banks do not agree on the codes: the loan-loss provision is
    3.02.05 for BBAS3 and 3.02.04 for BBDC4 (#27). The provision sits *inside* 3.02
    and is deducted before the 3.03 result, which is why ``gross_profit`` for a
    bank is net of it. These CVM lines remain faithful statement facts, but the
    calculator does not combine them into approximate bank ratios. Average
    earning assets, the full efficiency perimeter and average credit exposure
    require an explicit public regulatory/issuer disclosure (ADR 0058).

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
        ebit=None,  # 3.05 is pre-tax profit, never EBIT (ADR 0058)
        # A bank files the CPC 03-labelled total directly at 1.01 and has no
        # current/non-current split from which to isolate broader investments.
        cash_equivalents=_mul(_by_code(bpa, "1.01"), bpa_s),
        loan_loss_provision=_mul(_child_by_name(dre, "3.02", "provisao"), dre_s),
        fee_income=_mul(_child_by_name(dre, "3.04", "prestacao de servicos"), dre_s),
        personnel_expense=_mul(_child_by_name(dre, "3.04", "pessoal"), dre_s),
        admin_expense=_mul(_child_by_name(dre, "3.04", "administrativas"), dre_s),
        loan_book=_loan_book(bpa, bpa_s),
        bank_ratio_null_reason=NullReason.MISSING_REGULATORY_DISCLOSURE,
        unmapped_fields=_BANK_UNMAPPED_FIELDS,
    )


# Where the pre-2020 charts park the credit portfolio, newest first. Both are a
# line *already* net of its provision ("Empréstimos a Clientes Líquidos de
# Provisão"), so nothing is subtracted from them — and nothing may be: under
# ``1.03`` the sibling ``Empréstimos a Instituições Financeiras Líquidos de
# Provisão`` also carries the word, and subtracting it would take R$20 bn of
# interbank lending off BBAS3's book (#155).
_LEGACY_LOAN_BOOK_PARENTS = ("1.02.03", "1.03")


def _loan_book(bpa: Accounts, scale: Decimal) -> Decimal | None:
    """The credit portfolio, net of the provision carried against it.

    From 2020 both banks file the loan book under "Ativos Financeiros ao Custo
    Amortizado" (1.02.04) with the balance-sheet provision as its sibling — but only
    BBDC4 fills that provision line in (BBAS3 files zero there, its portfolio already
    net). Subtracting it where it is filed puts the two banks on one basis: what the
    bank still expects to collect.

    Before that the same portfolio sat under a different parent and a different name
    twice over (#155): ``1.02.03`` "Ativos Financeiros Avaliados ao Custo Amortizado"
    in 2018–2019, and ``1.03`` "Empréstimos e Recebíveis" up to 2017. The needle for
    both is "clientes", which is what separates the customer book from the interbank
    lending filed beside it — the two banks word the rest of the line differently.
    """
    gross = _child_by_name(bpa, "1.02.04", "operacoes de credito")
    if gross is not None:
        provision = _child_by_name(bpa, "1.02.04", "provisao") or Decimal(0)
        return _mul(gross + provision, scale)  # the provision is filed negative
    for parent in _LEGACY_LOAN_BOOK_PARENTS:
        net = _child_by_name(bpa, parent, "clientes")
        if net is not None:
            return _mul(net, scale)
    return None


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
        cash_equivalents=_mul(_by_code(bpa, "1.01.01"), bpa_s),
        current_financial_investments=_mul(_by_code(bpa, "1.01.02"), bpa_s),
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
    return replace(
        base,
        ebit=ebit,
        ebitda=ebitda,
        dep_amort=dep_amort,
        # CPC 03 eligibility is the line the issuer itself classifies as cash and
        # cash equivalents. The broader 1.01.02 investments remain visible but do
        # not silently reduce net debt (ADR 0057).
        cash_equivalents=_mul(_by_code(bpa, "1.01.01"), bpa_s),
        current_financial_investments=_mul(_by_code(bpa, "1.01.02"), bpa_s),
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
        sector_resolver: Callable[[str], Sector],
        registrant_resolver: RegistrantResolver = no_registrant,
        per_share_resolver: PerShareResolver = _no_per_share_components,
    ) -> None:
        self._collection = collection
        # The sector only seeds the ``expected_regime`` fallback (the filed regime,
        # read off the statement, decides applicability). The CLI injects a
        # registry-backed resolver, unconditionally (#212).
        self._sector_resolver = sector_resolver
        # Which registrant's filings to read (ADR 0030) — the same resolution, from
        # the same registry, that the sector one uses.
        self._registrant = registrant_resolver
        self._per_share = per_share_resolver

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
        cursor = self._collection.find(mirror_filter(ticker, self._registrant))
        docs: list[Mapping[str, Any]] = await cursor.to_list(None)
        sector = self._sector_resolver(ticker)
        components = self._per_share(ticker)

        by_period: dict[str, dict[str, Any]] = {}
        doc_type: dict[str, str | None] = {}
        best: dict[tuple[str, str], tuple[int, int, int, datetime]] = {}
        # CPC 41 retrospectively adjusts split-like events in every period shown.
        # A later DFP's PENULTIMO column can therefore be the authoritative LPA
        # for the previous exercise even though every other account keeps the
        # exercise's own filing. Key it by the column's actual period end and
        # prefer the latest consolidated presentation.
        per_share_by_period: dict[str, Mapping[str, Any]] = {}
        per_share_best: dict[str, tuple[int, str, int, int, datetime]] = {}
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
            if module == "DRE":
                period_end = payload.get("period_end_date")
                per_share_ref = period_end if isinstance(period_end, str) else ref
                balance = payload.get("balance_type")
                version = payload.get("version")
                per_share_rank = (
                    _BALANCE_RANK.get(balance if isinstance(balance, str) else "", 0),
                    ref,
                    version if isinstance(version, int) else 0,
                    _span_months(payload),
                    fetched,
                )
                accounts = _accounts_of(payload)
                basic, _basic_reason = _filed_per_share(accounts, "3.99.01", components)
                diluted, _diluted_reason = _filed_per_share(
                    accounts, "3.99.02", components
                )
                # An empty comparative is not a retrospective restatement and
                # must not erase a valid result from the exercise's own DFP.
                if basic is not None or diluted is not None:
                    if (
                        per_share_ref not in per_share_best
                        or per_share_rank > per_share_best[per_share_ref]
                    ):
                        per_share_best[per_share_ref] = per_share_rank
                        per_share_by_period[per_share_ref] = payload
            if _ordem(payload) != _CURRENT_PERIOD:
                continue  # the comparative describes the prior period, not this one
            key = (ref, module)
            rank = (
                _dre_rank(payload, fetched)
                if module == "DRE"
                else _rank(payload, fetched)
            )
            if key not in best or rank > best[key]:
                best[key] = rank
                by_period.setdefault(ref, {})[module] = payload
                tag = payload.get("document_type")
                if isinstance(tag, str):
                    doc_type[ref] = tag
            if module == "DMPL" and payload.get("balance_type") == "individual":
                if ref not in parent_dmpl_best or rank > parent_dmpl_best[ref]:
                    parent_dmpl_best[ref] = rank
                    parent_dmpl[ref] = payload

        for ref, modules in by_period.items():
            if ref in parent_dmpl:
                modules["DMPL"] = parent_dmpl[ref]

        return [
            (
                doc_type.get(ref),
                standardize(
                    modules,
                    sector,
                    date.fromisoformat(ref),
                    per_share_components=components,
                    per_share_accounts=_accounts_of(
                        per_share_by_period.get(ref, modules.get("DRE"))
                    ),
                ),
            )
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


def _dre_rank(
    payload: Mapping[str, Any], fetched: datetime
) -> tuple[int, int, int, datetime]:
    """Prefer a consolidated DRE before comparing versions (ADR 0054).

    CPC 41 requires the consolidated disclosure when both statement scopes
    exist. An individual-only amendment must therefore not displace an already
    available consolidated statement; within one scope, the latest amendment
    and longest accumulated column retain the normal ranking.
    """
    version = payload.get("version")
    balance = payload.get("balance_type")
    return (
        _BALANCE_RANK.get(balance if isinstance(balance, str) else "", 0),
        version if isinstance(version, int) else 0,
        _span_months(payload),
        fetched,
    )
