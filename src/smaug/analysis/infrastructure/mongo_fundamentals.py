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
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

from smaug.analysis.domain.bank_ratios import resolve_bank_ratios
from smaug.analysis.domain.financials import (
    AccountingRegime,
    BankStatementInputs,
    Cpc41Disclosure,
    DebtBlocker,
    DebtCoverageEvidence,
    DebtIdentityStatus,
    DebtInstrument,
    DebtLineEvidence,
    DebtLineRole,
    InsuranceUnderwritingEvidence,
    InsuranceUnderwritingStatus,
    IssuerIdentity,
    SourceAccountEvidence,
    SourceAccountRef,
    SourceAccountStatus,
    StandardizedFinancials,
    expected_regime,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.infrastructure.mirror import mirror_filter, no_registrant
from smaug.portfolio.domain.company import RegistrantResolver
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.securities import (
    PeriodShareClassesResolver,
    no_period_share_classes,
)
from smaug.portfolio.domain.share_classes import PerShareClass, UnitComponent

_STATEMENTS = ("BPA", "BPP", "DRE", "DFC", "DMPL", "DVA")

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
PerShareClassesResolver = Callable[[str], tuple[PerShareClass, ...]]
PerShareRightsReasonResolver = Callable[[str], NullReason]
IssuerResolver = Callable[[str], IssuerIdentity | None]


def _no_issuer(_ticker: str) -> IssuerIdentity | None:
    return None


class RawCollection(Protocol):
    """Minimal read surface over the ``raw_ingestions`` collection."""

    def find(self, filter: Mapping[str, Any], /) -> Any: ...


def _no_per_share_components(_ticker: str) -> tuple[UnitComponent, ...]:
    return ()


def _no_per_share_classes(_ticker: str) -> tuple[PerShareClass, ...]:
    return ()


def _missing_economic_rights(_ticker: str) -> NullReason:
    return NullReason.MISSING_ECONOMIC_RIGHTS


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


def _account_by_code(accounts: Accounts, code: str) -> Mapping[str, Any] | None:
    """Return the first raw account with an exact CVM code."""
    for account in accounts:
        if str(account.get("code")) == code:
            return account
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


def _is_distribution_name(name: str) -> bool:
    folded = _fold(name)
    return (
        "dividendo" in folded
        or bool(re.search(r"\bj(?:s)?cp\b", folded))
        or ("capital proprio" in folded and ("juro" in folded or "remuner" in folded))
        or "distribuicao de lucro" in folded
        or "lucros distribuid" in folded
    )


def _is_minority_distribution(name: str) -> bool:
    folded = _fold(name)
    return "nao control" in folded or "minorit" in folded


def _is_paid_distribution(account: Mapping[str, Any]) -> bool:
    code = str(account.get("code", ""))
    name = _fold(str(account.get("name", "")))
    if not _is_distribution_name(name):
        return False
    payment = "pag" in name or "pgto" in name
    if any(
        word in name
        for word in (
            "receb",
            "revers",
            "prescrit",
            "convers",
            "variacao",
            "ressarc",
            "a pagar",
            "a serem pag",
            "aumento de capital",
            "exercicios seguintes",
            "de controladas",
            "de coligadas",
        )
    ):
        return False
    if not payment and any(
        word in name for word in ("propost", "provis", "constitui", "destina")
    ):
        return False
    if name.strip().startswith(("imposto", "tribut", "irrf", "ir ")):
        return False
    # Gross JCP is still a distribution. A standalone tax or an inseparable
    # dividend-plus-tax amount does not establish that full distribution.
    if (
        any(word in name for word in ("imposto", "tribut", "irrf", "retido"))
        and "bruto" not in name
    ):
        return False
    if payment:
        # Explicit cash payments can be filed in another cash-flow section.
        # Indirect operating adjustments and subsidiary receipts stay out.
        return code.startswith(("6.01.", "6.02.", "6.03.")) and not code.startswith(
            "6.01.01."
        )
    value = _finite_account_value(account)
    # A financing outflow identifies the paid event without a payment verb.
    # Positive short labels describe incoming cash, not a distribution paid.
    return code.startswith("6.03.") and (value is None or value <= 0)


def _paid_distribution_accounts(dfc: Accounts) -> Accounts:
    return tuple(account for account in dfc if _is_paid_distribution(account))


def _dividends_paid(dfc: Accounts) -> Decimal | None:
    """Resolve cash distributions to controllers without mixing paid/declared.

    Explicit financing outflows may name dividends/JCP or profit distributions
    without a payment verb. Preserve a filed zero and require every selected
    component to be readable. A parent represents its details once; an explicit
    minority split is subtracted from a generic parent and reconciled with any
    controllers' detail. Unobserved amounts are never zeros.
    """
    candidates = _paid_distribution_accounts(dfc)
    selected: list[str] = []
    total = Decimal(0)
    found = False
    for account in sorted(candidates, key=lambda a: str(a.get("code", "")).count(".")):
        code = str(account.get("code", ""))
        name = str(account.get("name", ""))
        if _is_minority_distribution(name) or any(_inside(code, p) for p in selected):
            continue
        value = _finite_account_value(account)
        if value is None:
            return None
        if any(word in _fold(name) for word in ("emprest", "debentur", "arrendamento")):
            return None
        # An unsigned/positive short financing label does not prove an outflow.
        if value > 0 and not ("pag" in _fold(name) or "pgto" in _fold(name)):
            return None
        same_code = [a for a in candidates if str(a.get("code", "")) == code]
        if any(
            _finite_account_value(a) != value
            or _is_minority_distribution(str(a.get("name", "")))
            for a in same_code
        ):
            return None
        children = [
            a for a in candidates if str(a.get("code", "")).startswith(code + ".")
        ]
        minorities = [
            a for a in children if _is_minority_distribution(str(a.get("name", "")))
        ]
        minorities = [
            a
            for a in minorities
            if not any(
                str(a.get("code", "")).startswith(str(b.get("code", "")) + ".")
                for b in minorities
                if b is not a
            )
        ]
        amount = abs(value)
        if minorities:
            amounts = [_finite_account_value(a) for a in minorities]
            if any(v is None or v > 0 for v in amounts):
                return None
            if len({str(a.get("code", "")) for a in minorities}) != len(minorities):
                return None
            amount -= sum((abs(v) for v in amounts if v is not None), Decimal(0))
            if amount < 0:
                return None
            for child in children:
                label = _fold(str(child.get("name", "")))
                if "controlador" in label and not _is_minority_distribution(label):
                    child_value = _finite_account_value(child)
                    if child_value is None or abs(child_value) != amount:
                        return None
        total += amount
        found = True
        selected.append(code)
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


def _dep_amort_accounts(dfc: Accounts) -> tuple[Mapping[str, Any], ...]:
    """Return the DFC rows accepted as D&A add-backs, without double counting."""
    selected: list[Mapping[str, Any]] = []
    selected_codes: list[str] = []
    for account in sorted(dfc, key=lambda item: str(item.get("code", "")).count(".")):
        code = str(account.get("code", ""))
        if not code.startswith("6.01"):
            continue
        if any(code.startswith(f"{parent}.") for parent in selected_codes):
            continue
        name = _fold(_without_parentheticals(str(account.get("name", ""))))
        if not any(needle in name for needle in _DEP_AMORT_NEEDLES):
            continue
        selected.append(account)
        selected_codes.append(code)
    return tuple(selected)


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
    selected = _dep_amort_accounts(dfc)
    if len({str(account.get("code", "")) for account in selected}) != len(selected):
        return None
    values = tuple(_dec(account.get("quantity")) for account in selected)
    if not values or any(value is None or not value.is_finite() for value in values):
        return None
    return sum(
        (value for value in values if value is not None),
        Decimal(0),
    )


@dataclass(frozen=True, slots=True)
class _DepAmortResolution:
    value: Decimal | None
    statement: str
    found: tuple[SourceAccountRef, ...]
    expected: tuple[str, ...]
    formula: str | None = None


def _resolve_dep_amort(
    by_module: Mapping[str, Any], reference_date: date
) -> _DepAmortResolution:
    """Resolve the period's D&A from operating add-backs or aligned DVA facts."""
    dfc, scale = _accounts(by_module, "DFC"), _scale(by_module, "DFC")
    selected = _dep_amort_accounts(dfc)
    resolution = _DepAmortResolution(
        value=_mul(_dep_amort(dfc), scale),
        statement="DFC",
        found=tuple(
            SourceAccountRef(
                code=str(account.get("code", "")),
                name=str(account.get("name", "")),
                value=_mul(_dec(account.get("quantity")), scale),
                column=_account_column(account),
            )
            for account in selected
        ),
        expected=("scope=6.01", "label~depreciacao/amortizacao/exaustao"),
    )
    if resolution.value is not None:
        return resolution

    # D&A still uses the DFC flow clock during TTM isolation. Only a DVA from
    # the same filing scope and span can supply that input. Missing metadata
    # cannot establish compatibility, even for a filed zero.
    payloads = [by_module.get(module) for module in ("DRE", "DFC", "DVA")]
    if not all(isinstance(payload, Mapping) for payload in payloads):
        return resolution
    dre, dfc_payload, dva_payload = (
        payload for payload in payloads if isinstance(payload, Mapping)
    )
    for key in (
        "cvm_code",
        "balance_type",
        "document_type",
        "version",
        "currency",
        "period_start_date",
        "period_end_date",
        "reference_date",
    ):
        value = dre.get(key)
        if (
            value is None
            or value == ""
            or any(payload.get(key) != value for payload in (dfc_payload, dva_payload))
        ):
            return resolution
    if (
        dre.get("balance_type") not in _BALANCE_RANK
        or _iso_date(dre.get("period_end_date")) != reference_date
        or _iso_date(dre.get("reference_date")) != reference_date
        or _iso_date(dre.get("period_start_date")) is None
    ):
        return resolution
    start = _iso_date(dre.get("period_start_date"))
    assert start is not None
    size = dva_payload.get("currency_size")
    if start > reference_date or not isinstance(size, int) or size not in {1, 1000}:
        return resolution

    dva, dva_scale = _accounts(by_module, "DVA"), _scale(by_module, "DVA")
    direct = _account_by_code(dva, "7.04.01")
    amount = None if direct is None else _dec(direct.get("quantity"))
    formula = "-DVA[7.04.01]"
    codes: tuple[str, ...] = ("7.04.01",)
    if any(
        sum(str(account.get("code")) == code for account in dva) > 1
        for code in ("7.04.01", "7.04", "7.04.02")
    ):
        return resolution
    if direct is not None:
        label = _fold(str(direct.get("name", "")))
        if not all(needle in label for needle in _DEP_AMORT_NEEDLES):
            return resolution
    if amount is None or not amount.is_finite():
        # Retentions contain D&A and Other. Both filed components of this
        # identity are required; an omitted Other line is never a zero.
        parent = _account_by_code(dva, "7.04")
        other = _account_by_code(dva, "7.04.02")
        if (
            parent is None
            or other is None
            or "retenc" not in _fold(str(parent.get("name", "")))
            or _fold(str(other.get("name", ""))).strip() not in {"outras", "outros"}
        ):
            return resolution
        total, remainder = _dec(parent.get("quantity")), _dec(other.get("quantity"))
        if (
            total is None
            or remainder is None
            or not total.is_finite()
            or not remainder.is_finite()
        ):
            return resolution
        amount = total - remainder
        formula = "-(DVA[7.04] - DVA[7.04.02])"
        codes = ("7.04", "7.04.02")
    if amount > 0:
        return resolution
    return _DepAmortResolution(
        value=-amount * dva_scale,
        statement="DVA",
        found=_code_refs(dva, dva_scale, *codes),
        expected=(
            "code=7.04.01; or complete 7.04 - 7.04.02",
            *(
                f"{key}={dva_payload[key]}"
                for key in (
                    "cvm_code",
                    "balance_type",
                    "document_type",
                    "version",
                    "period_start_date",
                    "period_end_date",
                )
            ),
        ),
        formula=formula,
    )


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
        if not _is_distribution_name(name):
            continue
        value = _finite_account_value(account)
        if value is None:
            return None
        if value >= 0:
            continue
        key = (code, name)
        if key not in rows or abs(value) > abs(rows[key]):
            rows[key] = value
    selected = [
        (code, value)
        for (code, _), value in rows.items()
        if not any(
            code.startswith(parent + ".") for parent, _ in rows if parent != code
        )
    ]
    return sum((abs(value) for _, value in selected), Decimal(0))


def _capex_accounts(dfc: Accounts) -> tuple[Mapping[str, Any], ...]:
    """Return qualifying PP&E/intangible cash-out rows from the DFC."""
    return tuple(
        account
        for account in dfc
        if str(account.get("code", "")).startswith("6.02")
        and (
            "imob" in _fold(str(account.get("name", "")))
            or "intangiv" in _fold(str(account.get("name", "")))
        )
        and (_dec(account.get("quantity")) or Decimal(0)) <= 0
        and _dec(account.get("quantity")) is not None
    )


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
    selected = _capex_accounts(dfc)
    if not selected:
        return None
    return sum(
        (-(_dec(account.get("quantity")) or Decimal(0)) for account in selected),
        Decimal(0),
    )


_SOURCE_CONSUMERS: dict[str, tuple[str, ...]] = {
    "cfo": ("fcf", "price_to_fcf", "fcf_yield"),
    "capex": ("fcf", "price_to_fcf", "fcf_yield"),
    "dep_amort": (
        "ebitda_margin",
        "ebitda_cagr_5y",
        "net_debt_to_ebitda",
        "ev_ebitda",
    ),
    "ebitda": (
        "ebitda_margin",
        "ebitda_cagr_5y",
        "net_debt_to_ebitda",
        "ev_ebitda",
    ),
    "dividends_paid": (
        "payout_cash_paid_in_period",
        "company_cash_yield_paid_in_period",
    ),
    "dividends_declared": (
        "payout_declared_in_period",
        "company_yield_declared_in_period",
    ),
    "current_financial_investments": ("current_financial_investments",),
    "bank_net_interest": ("net_interest_margin",),
    "bank_earning_assets": ("net_interest_margin",),
    "bank_operating_expenses": ("efficiency_ratio",),
    "bank_operating_income": ("efficiency_ratio",),
    "bank_credit_loss": ("cost_of_risk",),
    "bank_gross_credit": ("cost_of_risk",),
    "bank_gross_credit_with_leases": ("cost_of_risk",),
    "bank_interest_result_annualized": ("net_interest_margin",),
    "average_earning_assets": ("net_interest_margin",),
    "bank_efficiency_expenses": ("efficiency_ratio",),
    "bank_efficiency_income": ("efficiency_ratio",),
    "credit_loss_expense_annualized": ("cost_of_risk",),
    "average_credit_portfolio": ("cost_of_risk",),
    "insurance_underwriting_activity": ("loss_ratio", "combined_ratio"),
    "earned_premium": ("loss_ratio", "combined_ratio"),
    "claims_incurred": ("loss_ratio", "combined_ratio"),
    "acquisition_costs": ("combined_ratio",),
    "insurance_admin_expenses": ("combined_ratio",),
}


def _matching_refs(
    accounts: Accounts,
    scale: Decimal,
    predicate: Callable[[Mapping[str, Any]], bool],
) -> tuple[SourceAccountRef, ...]:
    """Keep the raw code, label, and scaled value for matching source rows."""
    return tuple(
        SourceAccountRef(
            code=str(account.get("code", "")),
            name=str(account.get("name", "")),
            value=_mul(_dec(account.get("quantity")), scale),
            column=_account_column(account),
        )
        for account in accounts
        if predicate(account)
    )


def _code_refs(
    accounts: Accounts, scale: Decimal, *codes: str
) -> tuple[SourceAccountRef, ...]:
    wanted = frozenset(codes)
    return _matching_refs(accounts, scale, lambda a: str(a.get("code", "")) in wanted)


def _name_refs(
    accounts: Accounts,
    scale: Decimal,
    needle: str,
    *,
    parent: str | None = None,
) -> tuple[SourceAccountRef, ...]:
    folded = _fold(needle)
    prefix = None if parent is None else f"{parent}."
    return _matching_refs(
        accounts,
        scale,
        lambda account: (
            (prefix is None or str(account.get("code", "")).startswith(prefix))
            and folded in _fold(str(account.get("name", "")))
        ),
    )


def _source_entry(
    field: str,
    statement: str,
    expected: tuple[str, ...],
    found: tuple[SourceAccountRef, ...],
    value: Decimal | None,
    unmapped_fields: frozenset[str],
    *,
    parent_code: str | None = None,
    formula: str | None = None,
    dependencies: tuple[str, ...] = (),
    blocker: NullReason | None = None,
    derived: bool = False,
    force_unmapped: bool = False,
) -> SourceAccountEvidence:
    """Build one provenance record without changing the calculation result."""
    if derived:
        status = SourceAccountStatus.DERIVED
    elif field in unmapped_fields or force_unmapped:
        status = SourceAccountStatus.UNMAPPED
    elif value is not None:
        status = SourceAccountStatus.MAPPED
    elif found and all(ref.value is None for ref in found):
        status = SourceAccountStatus.PRESENT_UNREADABLE
    else:
        status = SourceAccountStatus.ABSENT

    if blocker is None and value is None and not derived:
        blocker = (
            NullReason.SOURCE_ACCOUNT_UNMAPPED
            if status is SourceAccountStatus.UNMAPPED
            else NullReason.SOURCE_ACCOUNT_ABSENT
        )
    return SourceAccountEvidence(
        field=field,
        statement=statement,
        status=status,
        expected=expected,
        found=found,
        parent_code=parent_code,
        formula=formula,
        dependencies=dependencies,
        blocker=blocker,
        consumer_indicators=_SOURCE_CONSUMERS.get(field, ()),
    )


def _root_blocker(
    financials: StandardizedFinancials, dependencies: tuple[str, ...]
) -> NullReason | None:
    """Return the first named root cause for a derived input."""
    for dependency in dependencies:
        if dependency in financials.unmapped_fields:
            return NullReason.SOURCE_ACCOUNT_UNMAPPED
        if getattr(financials, dependency, None) is None:
            return NullReason.SOURCE_ACCOUNT_ABSENT
    return None


def _statement_period_evidence(
    by_module: Mapping[str, Any], statement: str
) -> tuple[str, ...]:
    payload = by_module.get(statement)
    if not isinstance(payload, Mapping):
        return ()
    return tuple(
        f"{key}={payload[key]}"
        for key in (
            "cvm_code",
            "balance_type",
            "currency",
            "currency_size",
            "period_start_date",
            "period_end_date",
            "reference_date",
            "document_type",
            "version",
        )
        if payload.get(key) is not None
    )


def _source_account_evidence(
    by_module: Mapping[str, Any],
    financials: StandardizedFinancials,
    *,
    per_share_accounts: Accounts,
    per_share_components: Sequence[UnitComponent] = (),
    period_share_classes: Sequence[PerShareClass] = (),
    dep_amort_resolution: _DepAmortResolution | None = None,
) -> tuple[SourceAccountEvidence, ...]:
    """Inventory raw roots used by indicators and their derived blockers."""
    bpa, bpa_s = _accounts(by_module, "BPA"), _scale(by_module, "BPA")
    bpp, bpp_s = _accounts(by_module, "BPP"), _scale(by_module, "BPP")
    dre, dre_s = _accounts(by_module, "DRE"), _scale(by_module, "DRE")
    dfc, dfc_s = _accounts(by_module, "DFC"), _scale(by_module, "DFC")
    dmpl, dmpl_s = _accounts(by_module, "DMPL"), _scale(by_module, "DMPL")
    duplicate_counts = {
        module: _duplicate_count(by_module, module) for module in _STATEMENTS
    }
    regime = financials.filed_regime or expected_regime(financials.sector)
    entries: list[SourceAccountEvidence] = []
    seen: set[str] = set()

    def add(entry: SourceAccountEvidence) -> None:
        duplicate_count = duplicate_counts.get(entry.statement, 0)
        if duplicate_count:
            entry = replace(entry, duplicates_discarded=duplicate_count)
        if entry.field not in seen:
            entries.append(entry)
            seen.add(entry.field)

    add(
        _source_entry(
            "total_assets",
            "BPA",
            ("code=1",),
            _code_refs(bpa, bpa_s, "1"),
            financials.total_assets,
            financials.unmapped_fields,
        )
    )
    equity_refs = _matching_refs(
        bpp,
        bpp_s,
        lambda account: any(
            needle in _fold(str(account.get("name", "")))
            for needle in (
                "patrimonio liquido",
                "atribuido ao controlador",
                "nao control",
            )
        ),
    )
    add(
        _source_entry(
            "equity",
            "BPP",
            ("label~patrimonio liquido", "direct child~atribuido ao controlador"),
            equity_refs,
            financials.equity,
            financials.unmapped_fields,
        )
    )
    add(
        _source_entry(
            "equity_total",
            "BPP",
            ("label~patrimonio liquido",),
            _name_refs(bpp, bpp_s, "patrimonio liquido"),
            financials.equity_total,
            financials.unmapped_fields,
        )
    )
    income_refs = _matching_refs(
        dre,
        dre_s,
        lambda account: (
            any(
                marker in _fold(str(account.get("name", "")))
                for marker in _NET_INCOME_TOTAL_NAMES
            )
            or "socios da empresa controladora" in _fold(str(account.get("name", "")))
            or "nao controladores" in _fold(str(account.get("name", "")))
        ),
    )
    add(
        _source_entry(
            "net_income",
            "DRE",
            ("label~consolidated bottom line", "child~controllers/minority"),
            income_refs,
            financials.net_income,
            financials.unmapped_fields,
        )
    )
    add(
        _source_entry(
            "net_income_total",
            "DRE",
            ("label~consolidated bottom line",),
            income_refs,
            financials.net_income_total,
            financials.unmapped_fields,
        )
    )
    for field, code in (
        ("revenue", "3.01"),
        ("gross_profit", "3.03"),
    ):
        add(
            _source_entry(
                field,
                "DRE",
                (f"code={code}",),
                _code_refs(dre, dre_s, code),
                getattr(financials, field),
                financials.unmapped_fields,
            )
        )
    ebit_code = "3.07" if regime is AccountingRegime.INSURANCE else "3.05"
    add(
        _source_entry(
            "ebit",
            "DRE",
            (f"code={ebit_code}",),
            _code_refs(dre, dre_s, ebit_code),
            financials.ebit,
            financials.unmapped_fields,
        )
    )
    if regime is AccountingRegime.INSURANCE:
        underwriting = financials.insurance_underwriting_evidence
        aggregate_refs: tuple[SourceAccountRef, ...] = ()
        activity_blocker: NullReason | None = None
        if underwriting is not None:
            aggregate_refs = tuple(
                ref
                for ref in (
                    underwriting.revenue_aggregate,
                    underwriting.expense_aggregate,
                )
                if ref is not None
            )
            if underwriting.status is InsuranceUnderwritingStatus.ZERO_ACTIVITY:
                activity_blocker = NullReason.INAPPLICABLE_REGIME
        add(
            _source_entry(
                "insurance_underwriting_activity",
                "DRE",
                ("code=3.01", "code=3.02", "both explicit zero => no activity"),
                aggregate_refs,
                None,
                financials.unmapped_fields,
                formula="3.01 == 0 and 3.02 == 0",
                blocker=activity_blocker,
                derived=True,
            )
        )
        for field, codes, value in (
            (
                "earned_premium",
                ("3.01.01.01", "3.01.02.01"),
                financials.earned_premium,
            ),
            (
                "claims_incurred",
                ("3.02.01.01", "3.02.02.01"),
                financials.claims_incurred,
            ),
            (
                "acquisition_costs",
                ("3.02.01.02", "3.02.02.02"),
                financials.acquisition_costs,
            ),
            (
                "insurance_admin_expenses",
                ("3.04",),
                financials.insurance_admin_expenses,
            ),
        ):
            add(
                _source_entry(
                    field,
                    "DRE",
                    tuple(f"code={code}" for code in codes),
                    _code_refs(dre, dre_s, *codes),
                    value,
                    financials.unmapped_fields,
                )
            )
    cpc_refs_basic = _matching_refs(
        per_share_accounts,
        Decimal(1),
        lambda account: str(account.get("code", "")).startswith("3.99.01."),
    )
    cpc_refs_diluted = _matching_refs(
        per_share_accounts,
        Decimal(1),
        lambda account: str(account.get("code", "")).startswith("3.99.02."),
    )
    add(
        _source_entry(
            "eps_basic",
            "DRE",
            (
                "code=3.99.01.*",
                "class label or period-proved single-class per-lot disclosure",
                "reais_per_share = reais_per_1000_shares / 1000 for per-lot lines",
                *(f"period_class={item.value}" for item in period_share_classes),
                *(
                    f"component={item.quantity}*{item.per_share_class.value}"
                    for item in per_share_components
                ),
            ),
            cpc_refs_basic,
            financials.eps_basic,
            financials.unmapped_fields,
            blocker=financials.eps_basic_null_reason,
        )
    )
    add(
        _source_entry(
            "eps_diluted",
            "DRE",
            (
                "code=3.99.02.*",
                "class label or period-proved single-class per-lot disclosure",
                "reais_per_share = reais_per_1000_shares / 1000 for per-lot lines",
                *(f"period_class={item.value}" for item in period_share_classes),
                *(
                    f"component={item.quantity}*{item.per_share_class.value}"
                    for item in per_share_components
                ),
            ),
            cpc_refs_diluted,
            financials.eps_diluted,
            financials.unmapped_fields,
            blocker=financials.eps_diluted_null_reason,
        )
    )
    add(
        _source_entry(
            "cfo",
            "DFC",
            ("code=6.01",),
            _code_refs(dfc, dfc_s, "6.01"),
            financials.cfo,
            financials.unmapped_fields,
            parent_code="6.01",
        )
    )
    dep_resolution = dep_amort_resolution or _resolve_dep_amort(
        {key: value for key, value in by_module.items() if key != "DVA"},
        financials.reference_date,
    )
    add(
        _source_entry(
            "dep_amort",
            dep_resolution.statement,
            dep_resolution.expected,
            dep_resolution.found,
            financials.dep_amort,
            financials.unmapped_fields,
            parent_code="7.04" if dep_resolution.statement == "DVA" else "6.01",
            formula=dep_resolution.formula,
        )
    )
    capex_candidates = _matching_refs(
        dfc,
        dfc_s,
        lambda account: (
            str(account.get("code", "")).startswith("6.02")
            and (
                "imob" in _fold(str(account.get("name", "")))
                or "intangiv" in _fold(str(account.get("name", "")))
            )
        ),
    )
    add(
        _source_entry(
            "capex",
            "DFC",
            ("scope=6.02", "label~imobilizado/intangivel", "cash-out <= 0"),
            capex_candidates,
            financials.capex,
            financials.unmapped_fields,
            parent_code="6.02",
        )
    )
    add(
        _source_entry(
            "fcf",
            "derived",
            (),
            (),
            None,
            financials.unmapped_fields,
            formula="annualize(cfo - capex)",
            dependencies=("cfo", "capex"),
            blocker=_root_blocker(financials, ("cfo", "capex")),
            derived=True,
        )
    )
    add(
        _source_entry(
            "ebitda",
            "derived",
            (),
            (),
            financials.ebitda,
            financials.unmapped_fields,
            formula="ebit + dep_amort",
            dependencies=("ebit", "dep_amort"),
            blocker=_root_blocker(financials, ("ebit", "dep_amort")),
            derived=True,
        )
    )
    cash_code = "1.01" if regime is AccountingRegime.BANK else "1.01.01"
    add(
        _source_entry(
            "cash_equivalents",
            "BPA",
            (f"code={cash_code}",)
            + (
                ("or explicit current cash-and-equivalents aggregate",)
                if regime is not AccountingRegime.BANK
                else ()
            ),
            tuple(
                SourceAccountRef(
                    str(account.get("code", "")),
                    str(account.get("name", "")),
                    _mul(_finite_account_value(account), bpa_s),
                )
                for account in _cash_equivalent_accounts(bpa, regime)
            ),
            financials.cash_equivalents,
            financials.unmapped_fields,
        )
    )
    add(
        _source_entry(
            "current_financial_investments",
            "BPA",
            ("code=1.01.02",),
            _code_refs(bpa, bpa_s, "1.01.02"),
            financials.current_financial_investments,
            financials.unmapped_fields,
        )
    )
    if regime is not AccountingRegime.BANK:
        add(
            _source_entry(
                "current_assets",
                "BPA",
                ("code=1.01",),
                _code_refs(bpa, bpa_s, "1.01"),
                financials.current_assets,
                financials.unmapped_fields,
            )
        )
        add(
            _source_entry(
                "current_liabilities",
                "BPP",
                ("code=2.01",),
                _code_refs(bpp, bpp_s, "2.01"),
                financials.current_liabilities,
                financials.unmapped_fields,
            )
        )
    debt_refs: tuple[SourceAccountRef, ...] = ()
    if financials.debt_evidence is not None:
        debt_refs = tuple(
            SourceAccountRef(line.code, line.name, line.value)
            for line in (
                *financials.debt_evidence.used_lines,
                *financials.debt_evidence.excluded_lines,
            )
        )
    debt_blocker = financials.debt_coverage_null_reason
    if debt_blocker is None and financials.debt_evidence is not None:
        if financials.debt_evidence.primary_blocker is DebtBlocker.INAPPLICABLE_REGIME:
            debt_blocker = NullReason.INAPPLICABLE_REGIME
        elif (
            financials.debt_evidence.primary_blocker
            is DebtBlocker.INCOMPLETE_DEBT_COVERAGE
        ):
            debt_blocker = NullReason.INCOMPLETE_DEBT_COVERAGE
    add(
        _source_entry(
            "total_debt",
            "BPP",
            ("complete current/non-current perimeter",),
            debt_refs,
            financials.total_debt,
            financials.unmapped_fields,
            formula="sum(selected BPP perimeter)",
            blocker=debt_blocker,
        )
    )
    paid_refs = tuple(
        SourceAccountRef(
            str(a.get("code", "")),
            str(a.get("name", "")),
            _mul(_finite_account_value(a), dfc_s),
            column=_account_column(a),
        )
        for a in _paid_distribution_accounts(dfc)
    )
    add(
        _source_entry(
            "dividends_paid",
            "DFC",
            (
                "financing distribution; or explicit operational payment",
                *_statement_period_evidence(by_module, "DFC"),
            ),
            paid_refs,
            financials.dividends_paid,
            financials.unmapped_fields,
            formula="sum(nonoverlapping cash distributions to controllers)",
        )
    )

    def is_declared_source(account: Mapping[str, Any]) -> bool:
        return str(account.get("code", "")).startswith(
            _DECLARED_PREFIX
        ) and _is_distribution_name(str(account.get("name", "")))

    declared_refs = tuple(
        SourceAccountRef(
            str(a.get("code", "")),
            str(a.get("name", "")),
            _mul(_finite_account_value(a), dmpl_s),
            column=_account_column(a),
        )
        for a in dmpl
        if is_declared_source(a)
    )
    add(
        _source_entry(
            "dividends_declared",
            "DMPL",
            (
                "scope=5.04",
                "label~dividend/JCP",
                *_statement_period_evidence(by_module, "DMPL"),
            ),
            declared_refs,
            financials.dividends_declared,
            financials.unmapped_fields,
            parent_code="5.04",
        )
    )
    if regime is AccountingRegime.BANK:
        for field, expected in (
            ("bank_interest_result_annualized", "same-span interest result"),
            ("average_earning_assets", "average earning assets"),
            ("bank_efficiency_expenses", "full efficiency expenses"),
            ("bank_efficiency_income", "full efficiency income"),
            ("credit_loss_expense_annualized", "annualized credit loss"),
            ("average_credit_portfolio", "average credit portfolio"),
        ):
            add(
                _source_entry(
                    field,
                    "CVM_DERIVED",
                    (expected, "same-period paired perimeter"),
                    (),
                    getattr(financials, field),
                    financials.unmapped_fields,
                    blocker=NullReason.SOURCE_ACCOUNT_ABSENT,
                    derived=True,
                )
            )

    # Keep future mapper fields visible even before a selector is added. This
    # prevents a new deliberately skipped field from losing its provenance.
    for field in sorted(financials.unmapped_fields):
        if field not in seen:
            add(
                _source_entry(
                    field,
                    "CVM",
                    ("mapper deliberately does not read this field",),
                    (),
                    getattr(financials, field, None),
                    financials.unmapped_fields,
                )
            )
    return tuple(entries)


def _sum(*values: Decimal | None) -> Decimal | None:
    total = Decimal(0)
    present = False
    for value in values:
        if value is not None:
            total += value
            present = True
    return total if present else None


def _finite_account_value(account: Mapping[str, Any]) -> Decimal | None:
    value = _dec(account.get("quantity"))
    return value if value is not None and value.is_finite() else None


def _cash_equivalent_accounts(bpa: Accounts, regime: AccountingRegime) -> Accounts:
    code = "1.01" if regime is AccountingRegime.BANK else "1.01.01"
    canonical = _account_by_code(bpa, code)
    if canonical is not None and _finite_account_value(canonical) is not None:
        return (canonical,)
    if regime is AccountingRegime.BANK:
        return ()
    candidates = [
        account
        for account in bpa
        if str(account.get("code", "")).startswith("1.01.")
        and re.fullmatch(
            r"caixa e equivalentes(?: de caixa)?",
            _fold(str(account.get("name", ""))).strip(),
        )
    ]
    # A parent and its breakdown represent one amount; separate aggregates
    # must agree before one can be selected deterministically.
    return tuple(
        account
        for account in candidates
        if not any(
            str(account.get("code", "")).startswith(f"{other.get('code')}.")
            for other in candidates
            if other is not account and _finite_account_value(other) is not None
        )
    )


def _cash_equivalents(
    bpa: Accounts, scale: Decimal, regime: AccountingRegime
) -> Decimal | None:
    accounts = _cash_equivalent_accounts(bpa, regime)
    values = {
        value
        for account in accounts
        if (value := _finite_account_value(account)) is not None
    }
    if len(values) != 1:
        return None
    value = next(iter(values))
    assert value is not None
    return value * scale


def _is_tariff_or_tax_liability(name: str) -> bool:
    folded = _fold(name)
    return bool(
        re.match(r"^passivos? financeiros? (?:setori(?:al|ais)|do setor)\b", folded)
    ) or ("contribuicao" in folded and "seguridade social" in folded)


def _financial_bucket_components(bpp: Accounts, parent: Mapping[str, Any]) -> Accounts:
    code = str(parent.get("code", ""))
    value = _finite_account_value(parent)
    children = [
        account
        for account in bpp
        if str(account.get("code", "")).startswith(f"{code}.")
        and str(account.get("code", "")).count(".") == code.count(".") + 1
    ]
    if (
        value is None
        or value < 0
        or not children
        or len({str(child.get("code", "")) for child in children}) != len(children)
    ):
        return ()
    values = [_finite_account_value(child) for child in children]
    if any(amount is None or amount < 0 for amount in values):
        return ()
    if any(
        not (
            _is_explicit_debt_line(str(child.get("name", "")))
            or _is_non_debt_liability(str(child.get("name", "")))
        )
        for child in children
    ):
        return ()
    if sum((amount for amount in values if amount is not None), Decimal(0)) != value:
        return ()
    return tuple(children)


def _is_comprehensive_debt_name(name: str) -> bool:
    """Whether a BPP line declares the aggregate borrowing perimeter.

    CVM fixes the usual corporate parents at 2.01.04 and 2.02.01, but also
    publishes issuer-defined accounts and sector charts. The label, within each
    maturity section, is the invariant: both borrowings and financing must be
    named. A child breakdown named only "Debêntures" cannot prove that every
    other debt instrument was zero.
    """
    folded = _fold(name)
    return "emprest" in folded and "financiamento" in folded


def _debt_aggregate(
    accounts: Accounts, maturity_prefix: str
) -> Mapping[str, Any] | None:
    """The shallowest comprehensive debt line in one maturity bucket."""
    candidates = [
        account
        for account in accounts
        if str(account.get("code", "")).startswith(f"{maturity_prefix}.")
        and _is_comprehensive_debt_name(str(account.get("name", "")))
    ]
    return min(
        candidates,
        key=lambda account: str(account.get("code", "")).count("."),
        default=None,
    )


def _inside(code: str, parent: str) -> bool:
    return code == parent or code.startswith(f"{parent}.")


def _debt_instrument(name: str) -> DebtInstrument:
    """Classify a liability label without treating its CVM code as universal."""
    folded = _fold(name)
    if _is_tariff_or_tax_liability(name):
        return DebtInstrument.OTHER
    if "ressegur" in folded:
        return DebtInstrument.REINSURANCE
    if "contrato de seguro" in folded or "contratos de seguro" in folded:
        return DebtInstrument.INSURANCE_CONTRACT
    if "tecnic" in folded and ("provis" in folded or "reserva" in folded):
        return DebtInstrument.TECHNICAL_RESERVE
    if "previd" in folded or "pension" in folded or "pensao" in folded:
        return DebtInstrument.PENSION
    if "capitaliz" in folded:
        return DebtInstrument.CAPITALIZATION
    if (
        "derivativ" in folded
        or "swap" in folded
        or "hedge" in folded
        or "opcao" in folded
    ):
        return DebtInstrument.DERIVATIVE
    if _is_ambiguous_financial_liability(name):
        return DebtInstrument.GENERIC_FINANCIAL_LIABILITY
    if "debentur" in folded or "subordinad" in folded:
        return DebtInstrument.DEBENTURES_SUBORDINATED
    if "arrendamento" in folded or "lease" in folded:
        return DebtInstrument.LEASES
    if "mutuo" in folded:
        return DebtInstrument.MUTUOS
    if any(
        needle in folded
        for needle in (
            "securitiz",
            "cessao de receb",
            "recebiveis cedidos",
            "direitos creditori",
            "fidc",
        )
    ):
        return DebtInstrument.SECURITIZATION_RECEIVABLES
    if "aquisic" in folded or re.search(r"\bcci\b", folded):
        return DebtInstrument.ACQUISITION_DEBT_CCI
    if "emprest" in folded or "financiamento" in folded:
        return DebtInstrument.LOANS_FINANCING
    if any(
        needle in folded
        for needle in (
            "instrumento de divida",
            "instrumentos de divida",
            "nota promissoria",
            "notas promissorias",
            "commercial paper",
        )
    ):
        return DebtInstrument.LOANS_FINANCING
    return DebtInstrument.OTHER


def _is_ambiguous_financial_liability(name: str) -> bool:
    """A financial-liability bucket whose economic components are not named."""
    folded = _fold(name)
    if _is_tariff_or_tax_liability(name):
        return False
    if "passiv" not in folded or "financeir" not in folded:
        return False
    # These labels name non-debt instruments rather than an undecomposed bucket.
    return "derivativ" not in folded and "opco" not in folded


def _is_explicit_debt_line(name: str) -> bool:
    """A separately filed interest-bearing liability outside the aggregates."""
    folded = _fold(name)
    if _is_tariff_or_tax_liability(name):
        return False
    if "provis" in folded or "cancelamento" in folded:
        return False
    return _debt_instrument(name) in frozenset(
        {
            DebtInstrument.LOANS_FINANCING,
            DebtInstrument.DEBENTURES_SUBORDINATED,
            DebtInstrument.LEASES,
            DebtInstrument.MUTUOS,
            DebtInstrument.SECURITIZATION_RECEIVABLES,
            DebtInstrument.ACQUISITION_DEBT_CCI,
        }
    ) or any(
        needle in folded
        for needle in (
            "instrumento de divida",
            "instrumentos de divida",
            "nota promissoria",
            "notas promissorias",
            "commercial paper",
        )
    )


def _is_non_debt_liability(name: str) -> bool:
    """Whether a relevant liability is explicitly outside financing debt."""
    folded = _fold(name)
    if _is_tariff_or_tax_liability(name):
        return True
    if _debt_instrument(name) in frozenset(
        {
            DebtInstrument.INSURANCE_CONTRACT,
            DebtInstrument.REINSURANCE,
            DebtInstrument.TECHNICAL_RESERVE,
            DebtInstrument.PENSION,
            DebtInstrument.CAPITALIZATION,
            DebtInstrument.DERIVATIVE,
        }
    ):
        return True
    return "provis" in folded or "reserva" in folded


@dataclass(frozen=True, slots=True)
class _DebtAssessment:
    """Internal debt-perimeter result and its BPP evidence."""

    total_debt: Decimal | None
    null_reason: NullReason | None
    used_lines: tuple[DebtLineEvidence, ...]
    excluded_lines: tuple[DebtLineEvidence, ...]
    included_instruments: tuple[str, ...]
    primary_blocker: DebtBlocker | None
    secondary_blockers: tuple[DebtBlocker, ...]


def _debt_line(
    account: Mapping[str, Any],
    scale: Decimal,
    role: DebtLineRole,
    reason: DebtBlocker | None = None,
    instrument: DebtInstrument | None = None,
) -> DebtLineEvidence:
    name = str(account.get("name", ""))
    return DebtLineEvidence(
        code=str(account.get("code", "")),
        name=name,
        value=_mul(_finite_account_value(account), scale),
        role=role,
        reason=reason,
        instrument=_debt_instrument(name) if instrument is None else instrument,
    )


def _blocked_bpp_lines(
    bpp: Accounts,
    aggregate_codes: set[str],
    scale: Decimal,
    *,
    aggregate_missing: bool,
) -> list[DebtLineEvidence]:
    """Capture relevant candidates that were not accepted as used debt lines."""
    excluded: list[DebtLineEvidence] = []
    for account in bpp:
        code = str(account.get("code", ""))
        name = str(account.get("name", ""))
        if not code.startswith(("2.01.", "2.02.")):
            continue
        if any(_inside(code, parent) for parent in aggregate_codes):
            continue
        if _is_ambiguous_financial_liability(name):
            excluded.append(
                _debt_line(
                    account,
                    scale,
                    DebtLineRole.EXCLUDED_LIABILITY,
                    DebtBlocker.AMBIGUOUS_FINANCIAL_LIABILITY,
                )
            )
        elif _is_non_debt_liability(name):
            excluded.append(
                _debt_line(
                    account,
                    scale,
                    DebtLineRole.EXCLUDED_LIABILITY,
                    DebtBlocker.NON_DEBT_LIABILITY,
                )
            )
        elif aggregate_missing and _is_explicit_debt_line(name):
            # The old rule would not add an instrument when the aggregate proof
            # is absent. Keep that fact visible without changing the result.
            excluded.append(
                _debt_line(
                    account,
                    scale,
                    DebtLineRole.EXCLUDED_LIABILITY,
                    DebtBlocker.INCOMPLETE_DEBT_COVERAGE,
                )
            )
    return excluded


def _append_double_count_details(
    bpp: Accounts,
    selected_codes: set[str],
    scale: Decimal,
    excluded: list[DebtLineEvidence],
) -> None:
    """Record descendants that are already represented by a selected parent."""
    known_codes = {line.code for line in excluded}
    for account in sorted(bpp, key=lambda item: str(item.get("code", "")).count(".")):
        code = str(account.get("code", ""))
        if code in selected_codes or code in known_codes:
            continue
        if any(_inside(code, parent) for parent in selected_codes):
            excluded.append(
                _debt_line(
                    account,
                    scale,
                    DebtLineRole.EXCLUDED_LIABILITY,
                    DebtBlocker.CHILD_DETAIL_DOUBLE_COUNT,
                )
            )
            known_codes.add(code)


def _total_debt(bpp: Accounts, scale: Decimal) -> _DebtAssessment:
    """Complete explicit interest-bearing liabilities from the CVM BPP.

    Absence is never zero. Both the current and non-current borrowing aggregates
    must be filed, even when their published value is zero. Explicit debt-like
    liabilities outside those aggregates — most often CPC 06 lease liabilities
    placed under "Outras Obrigações" — are added once at their shallowest named
    level. A non-zero generic "Passivos financeiros" bucket makes the perimeter
    unknowable unless its complete, classified components reconcile exactly
    with the filed parent. Tariff deferrals and tax contributions are excluded
    from borrowed financing while remaining explicit in the source evidence.

    Insurance-contract/reserve, reinsurance, pension and capitalization
    liabilities do not match these debt labels: they arise from the products the
    filer issued, not from borrowed financing (ADR 0059).
    """
    aggregates = (
        _debt_aggregate(bpp, "2.01"),
        _debt_aggregate(bpp, "2.02"),
    )
    used: list[DebtLineEvidence] = []
    aggregate_codes: set[str] = set()
    aggregate_code_list: list[str] = []
    secondary: list[DebtBlocker] = []
    for account, role, missing in (
        (
            aggregates[0],
            DebtLineRole.CURRENT_AGGREGATE,
            DebtBlocker.MISSING_CURRENT_AGGREGATE,
        ),
        (
            aggregates[1],
            DebtLineRole.NON_CURRENT_AGGREGATE,
            DebtBlocker.MISSING_NON_CURRENT_AGGREGATE,
        ),
    ):
        if account is None:
            secondary.append(missing)
        else:
            code = str(account.get("code", ""))
            aggregate_codes.add(code)
            aggregate_code_list.append(code)
            used.append(_debt_line(account, scale, role))

    if secondary:
        excluded = _blocked_bpp_lines(
            bpp, aggregate_codes, scale, aggregate_missing=True
        )
        # A maturity aggregate that is present still represents all of its
        # descendants, even when the other maturity aggregate is absent. Keep
        # those descendants explicit in the evidence instead of losing the
        # double-counting boundary on the early null return.
        _append_double_count_details(bpp, aggregate_codes, scale, excluded)
        return _DebtAssessment(
            total_debt=None,
            null_reason=NullReason.INCOMPLETE_DEBT_COVERAGE,
            used_lines=tuple(used),
            excluded_lines=tuple(excluded),
            included_instruments=tuple(aggregate_code_list),
            primary_blocker=DebtBlocker.INCOMPLETE_DEBT_COVERAGE,
            secondary_blockers=tuple(secondary),
        )

    current = aggregates[0]
    non_current = aggregates[1]
    assert current is not None
    assert non_current is not None
    aggregate_values = (
        _finite_account_value(current),
        _finite_account_value(non_current),
    )
    missing_values = sum(value is None for value in aggregate_values)
    if missing_values:
        secondary = [DebtBlocker.MISSING_AGGREGATE_VALUE] * missing_values
        excluded = _blocked_bpp_lines(
            bpp, aggregate_codes, scale, aggregate_missing=False
        )
        _append_double_count_details(bpp, aggregate_codes, scale, excluded)
        return _DebtAssessment(
            total_debt=None,
            null_reason=NullReason.INCOMPLETE_DEBT_COVERAGE,
            used_lines=tuple(used),
            excluded_lines=tuple(excluded),
            included_instruments=tuple(aggregate_code_list),
            primary_blocker=DebtBlocker.INCOMPLETE_DEBT_COVERAGE,
            secondary_blockers=tuple(secondary),
        )

    resolved_buckets = {
        str(account.get("code", ""))
        for account in bpp
        if _is_ambiguous_financial_liability(str(account.get("name", "")))
        and _financial_bucket_components(bpp, account)
    }
    excluded = [
        replace(line, reason=DebtBlocker.CHILD_DETAIL_DOUBLE_COUNT)
        if line.code in resolved_buckets
        else line
        for line in _blocked_bpp_lines(
            bpp, aggregate_codes, scale, aggregate_missing=False
        )
    ]
    for account in bpp:
        code = str(account.get("code", ""))
        if not code.startswith(("2.01.", "2.02.")):
            continue
        if any(_inside(code, parent) for parent in aggregate_codes):
            continue
        if not _is_ambiguous_financial_liability(str(account.get("name", ""))):
            continue
        if code in resolved_buckets:
            continue
        value = _finite_account_value(account)
        if value is None or value != 0:
            secondary.append(DebtBlocker.AMBIGUOUS_FINANCIAL_LIABILITY)

    extra_total = Decimal(0)
    selected_codes: list[str] = []
    for account in sorted(bpp, key=lambda item: str(item.get("code", "")).count(".")):
        code = str(account.get("code", ""))
        if not code.startswith(("2.01.", "2.02.")):
            continue
        if any(_inside(code, parent) for parent in aggregate_codes):
            continue
        if any(_inside(code, parent) for parent in selected_codes):
            continue
        if not _is_explicit_debt_line(str(account.get("name", ""))):
            continue
        value = _finite_account_value(account)
        if value is None:
            excluded.append(
                _debt_line(
                    account,
                    scale,
                    DebtLineRole.EXCLUDED_LIABILITY,
                    DebtBlocker.UNREADABLE_EXPLICIT_INSTRUMENT,
                )
            )
            secondary.append(DebtBlocker.UNREADABLE_EXPLICIT_INSTRUMENT)
            continue
        extra_total += value
        selected_codes.append(code)
        used.append(_debt_line(account, scale, DebtLineRole.INCLUDED_INSTRUMENT))

    if secondary:
        _append_double_count_details(
            bpp, aggregate_codes | set(selected_codes), scale, excluded
        )
        return _DebtAssessment(
            total_debt=None,
            null_reason=NullReason.INCOMPLETE_DEBT_COVERAGE,
            used_lines=tuple(used),
            excluded_lines=tuple(excluded),
            included_instruments=(*aggregate_code_list, *selected_codes),
            primary_blocker=DebtBlocker.INCOMPLETE_DEBT_COVERAGE,
            secondary_blockers=tuple(secondary),
        )

    current_value = aggregate_values[0]
    non_current_value = aggregate_values[1]
    assert current_value is not None
    assert non_current_value is not None
    _append_double_count_details(
        bpp, aggregate_codes | set(selected_codes), scale, excluded
    )
    return _DebtAssessment(
        total_debt=(current_value + non_current_value + extra_total) * scale,
        null_reason=None,
        used_lines=tuple(used),
        excluded_lines=tuple(excluded),
        included_instruments=(*aggregate_code_list, *selected_codes),
        primary_blocker=None,
        secondary_blockers=(),
    )


def _debt_evidence(
    base: StandardizedFinancials,
    regime: AccountingRegime,
    assessment: _DebtAssessment,
) -> DebtCoverageEvidence:
    return DebtCoverageEvidence(
        regime=regime,
        regime_source=base.regime_source,
        used_lines=assessment.used_lines,
        excluded_lines=assessment.excluded_lines,
        included_instruments=assessment.included_instruments,
        primary_blocker=assessment.primary_blocker,
        secondary_blockers=assessment.secondary_blockers,
    )


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


_ACCOUNT_IDENTITY_ALIASES = frozenset(
    {
        "code",
        "CD_CONTA",
        "name",
        "DS_CONTA",
        "quantity",
        "VL_CONTA",
        "level",
        "is_fixed",
        "ST_CONTA_FIXA",
        "column",
        "COLUNA_DF",
    }
)


def _identity_value(value: Any) -> str:
    """Render one source value into a deterministic identity component."""
    if value is None:
        return "<none>"
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return (
            "<mapping:"
            + ",".join(f"{key!s}={_identity_value(item)}" for key, item in items)
            + ">"
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "<sequence:" + ",".join(_identity_value(item) for item in value) + ">"
    return f"<{type(value).__name__}:{value!r}>"


def _first_account_value(
    account: Mapping[str, Any], *names: str
) -> Any:  # genuinely untyped source payload
    for name in names:
        if name in account:
            return account[name]
    return None


def _account_column(account: Mapping[str, Any]) -> str | None:
    """Read the DMPL column under either the parsed or source field name."""
    value = _first_account_value(account, "column", "COLUNA_DF")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _account_fixed_flag(account: Mapping[str, Any]) -> Any:  # source payload value
    value = _first_account_value(account, "is_fixed", "ST_CONTA_FIXA")
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in {"S", "SIM", "TRUE", "1"}:
            return True
        if normalized in {"N", "NAO", "NÃO", "FALSE", "0"}:
            return False
    return value


def _source_cell_identity(
    module: str, payload: Mapping[str, Any], account: Mapping[str, Any]
) -> tuple[str, ...]:
    """Identify one CVM account cell without crossing filing boundaries.

    The parser intentionally preserves repeated rows in the raw mirror. Analysis
    may remove only an exact repeated source cell, so filing metadata and every
    account identity-bearing value participate in this key. In particular,
    ``COLUNA_DF`` distinguishes DMPL matrix cells that share a code and label.
    """
    payload_identity = tuple(
        f"payload:{str(key)}={_identity_value(value)}"
        for key, value in sorted(payload.items(), key=lambda item: str(item[0]))
        if key != "accounts"
    )
    canonical: dict[str, Any] = {
        "code": _first_account_value(account, "code", "CD_CONTA"),
        "name": _first_account_value(account, "name", "DS_CONTA"),
        "quantity": _first_account_value(account, "quantity", "VL_CONTA"),
        "level": _first_account_value(account, "level"),
        "is_fixed": _account_fixed_flag(account),
    }
    if module.upper() == "DMPL" or any(
        key in account for key in ("column", "COLUNA_DF")
    ):
        canonical["column"] = _account_column(account)
    account_identity = tuple(
        f"account:{key}={_identity_value(value)}"
        for key, value in sorted(canonical.items())
    )
    extras = tuple(
        f"account-extra:{str(key)}={_identity_value(value)}"
        for key, value in sorted(account.items(), key=lambda item: str(item[0]))
        if str(key) not in _ACCOUNT_IDENTITY_ALIASES
    )
    return (f"module:{module}", *payload_identity, *account_identity, *extras)


def _deduplicate_accounts(
    module: str,
    payload: Mapping[str, Any],
    accounts: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], ...], int]:
    """Drop only exact repeated source cells for one selected filing.

    This function is deliberately called after the reader has selected the
    authoritative filing. It never compares rows across versions, periods,
    statement scopes, or ``ORDEM_EXERC`` values, and it never mutates the raw
    payload. The first occurrence remains in source order for stable evidence.
    """
    seen: set[tuple[str, ...]] = set()
    retained: list[Mapping[str, Any]] = []
    discarded = 0
    for account in accounts:
        identity = _source_cell_identity(module, payload, account)
        if identity in seen:
            discarded += 1
            continue
        seen.add(identity)
        retained.append(account)
    return tuple(retained), discarded


def _duplicate_count(by_module: Mapping[str, Any], module: str) -> int:
    payload = by_module.get(module)
    if not isinstance(payload, Mapping):
        return 0
    accounts = _accounts_of(payload)
    return _deduplicate_accounts(module, payload, accounts)[1]


def _accounts(by_module: Mapping[str, Any], module: str) -> Accounts:
    payload = by_module.get(module)
    if not isinstance(payload, Mapping):
        return []
    accounts = _accounts_of(payload)
    return _deduplicate_accounts(module, payload, accounts)[0]


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
    return (
        accounts
        if isinstance(accounts, Sequence)
        and not isinstance(accounts, (str, bytes, bytearray))
        else ()
    )


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
    *,
    period_classes: Sequence[PerShareClass] = (),
    missing_rights_reason: NullReason = NullReason.MISSING_ECONOMIC_RIGHTS,
) -> tuple[Decimal | None, NullReason | None]:
    """One filed CPC 41 result for a share class or composed unit.

    CVM does not keep class labels in stable code positions: ``.01`` can be ON,
    PN or PNA. The label is therefore authoritative and duplicate conflicting
    values are rejected. Values are already reais per action and deliberately
    bypass the DRE's thousand-real scale.
    """
    if not components:
        return None, missing_rights_reason

    values, reason = _filed_per_share_values(dre, prefix, period_classes=period_classes)
    if values is None:
        if missing_rights_reason is NullReason.UNRESOLVED_SHARE_CLASS:
            return None, missing_rights_reason
        return None, reason

    total = Decimal(0)
    for component in components:
        value = _per_share_value(values, component.per_share_class, period_classes)
        if value is None:
            return None, missing_rights_reason
        total += Decimal(component.quantity) * value
    return total, None


def _per_share_value(
    values: Mapping[PerShareClass, Decimal],
    target: PerShareClass,
    period_classes: Sequence[PerShareClass],
) -> Decimal | None:
    """Select a generic PN value only with filed period-class proof.

    An exact CPC 41 label always wins. A generic PN can stand for PNA/PNB only
    when that FCA year names one preferred class, or when every other relevant
    preferred subclass files the same result as PN. This is the bounded rule
    audited in #304; it never infers rights from a ticker suffix alone.
    """
    exact = values.get(target)
    if exact is not None:
        return exact
    return _generic_preferred_value(values, target, period_classes)


def _generic_preferred_value(
    values: Mapping[PerShareClass, Decimal],
    target: PerShareClass,
    period_classes: Sequence[PerShareClass],
) -> Decimal | None:
    """Return a generic PN value only when it is the audited subclass match."""
    generic = values.get(PerShareClass.PREFERRED)
    if generic is None or target not in {
        PerShareClass.PREFERRED_A,
        PerShareClass.PREFERRED_B,
    }:
        return None

    preferred = {item for item in period_classes if item is not PerShareClass.ORDINARY}
    if preferred == {target}:
        return generic

    other_subclasses = preferred - {target, PerShareClass.PREFERRED}
    if (
        PerShareClass.PREFERRED not in preferred
        and other_subclasses
        and all(values.get(other) == generic for other in other_subclasses)
    ):
        return generic
    return None


def _filed_per_share_values(
    dre: Accounts, prefix: str, *, period_classes: Sequence[PerShareClass] = ()
) -> tuple[dict[PerShareClass, Decimal] | None, NullReason | None]:
    """Read one uncomposed CPC 41 result by its class labels.

    The caller that composes a security may need only one class. The weighted
    denominator proof, however, needs the complete issuer disclosure, so this
    helper exposes every unambiguous class leaf before the target security is
    selected.
    """
    values: dict[PerShareClass, set[Decimal]] = {}
    expected_level = prefix.count(".") + 1
    for account in dre:
        code = str(account.get("code", ""))
        if not code.startswith(f"{prefix}.") or code.count(".") != expected_level:
            continue
        label = _fold(str(account.get("name", "")))
        per_share_class = _PER_SHARE_LABELS.get(label)
        divisor = Decimal(1)
        # A per-lot disclosure identifies no class by itself. The period FCA
        # must prove exactly one class before its reais-per-1,000-shares amount
        # can be normalized into the same per-share result.
        if label == "lucro por lote de mil acoes" and len(set(period_classes)) == 1:
            per_share_class = period_classes[0]
            divisor = Decimal(1000)
        if per_share_class is None:
            continue
        value = _dec(account.get("quantity"))
        if value is None or not value.is_finite():
            return None, NullReason.MISSING_CPC41_DISCLOSURE
        values.setdefault(per_share_class, set()).add(value / divisor)

    if not values:
        return None, NullReason.MISSING_CPC41_DISCLOSURE

    result: dict[PerShareClass, Decimal] = {}
    for per_share_class, candidates in values.items():
        if len(candidates) != 1:
            return None, NullReason.MISSING_ECONOMIC_RIGHTS
        result[per_share_class] = next(iter(candidates))
    return result, None


def _complete_per_share_labels(
    dre: Accounts, prefix: str, period_classes: Sequence[PerShareClass]
) -> bool:
    return all(
        _fold(str(account.get("name", ""))) in _PER_SHARE_LABELS
        or (
            _fold(str(account.get("name", ""))) == "lucro por lote de mil acoes"
            and len(set(period_classes)) == 1
        )
        for account in dre
        if str(account.get("code", "")).startswith(f"{prefix}.")
    )


def _reconciled_cpc41(
    dre: Accounts,
    components: Sequence[UnitComponent],
    company_classes: Sequence[PerShareClass],
    period_classes: Sequence[PerShareClass],
) -> Cpc41Disclosure | None:
    """Prove that a filed class result can support strict TTM assembly.

    When every listed economic class reports the same basic result, the
    controller-attributable profit divided by that result is the issuer's
    aggregate weighted denominator. This is a reconciliation of the issuer's
    own CPC 41 disclosure, not a reconstruction from closing capital snapshots;
    it therefore does not invent a movement date or treat outstanding shares as
    a weighted average. A unit still carries the sum of its declared class
    quantities.

    Share-day reconstruction of diluted EPS requires its complete class
    disclosure to equal basic EPS because potential-share numerator adjustments
    are unavailable in the structured mirror. A filed annual result for the
    identical TTM span is selected directly by the TTM builder.
    """
    if not components:
        return None

    classes = tuple(dict.fromkeys(company_classes))
    basic_values, _basic_reason = _filed_per_share_values(
        dre, "3.99.01", period_classes=period_classes
    )
    multiplier = sum(
        (Decimal(component.quantity) for component in components), Decimal(0)
    )
    if multiplier <= 0:
        return None
    diluted_values, _diluted_reason = _filed_per_share_values(
        dre, "3.99.02", period_classes=period_classes
    )

    # Resolve equivalent labels across the complete issuer class set, using
    # the same period FCA proof as the security result. A generic PN can then
    # represent PNA/PNB inside a unit without demanding identical raw labels.
    # Every filed recognized class must still agree; no unequal class is dropped.
    complete_labels = _complete_per_share_labels(dre, "3.99.01", period_classes)
    if classes and basic_values is not None and complete_labels:
        basic_bases = {
            _per_share_value(basic_values, item, period_classes) for item in classes
        }
        basic_bases.update(basic_values.values())
        if len(basic_bases) == 1 and None not in basic_bases:
            basic_base = next(iter(basic_bases))
            diluted_base: Decimal | None = None
            if diluted_values is not None and _complete_per_share_labels(
                dre, "3.99.02", period_classes
            ):
                diluted_bases = {
                    _per_share_value(diluted_values, item, period_classes)
                    for item in classes
                }
                diluted_bases.update(diluted_values.values())
                if diluted_bases == {basic_base}:
                    diluted_base = basic_base
            return Cpc41Disclosure(
                basic_base_eps=basic_base,
                diluted_base_eps=diluted_base,
                security_multiplier=multiplier,
            )

    # The #304 exception is deliberately narrower than general class-result
    # reconciliation.  A plain PNA/PNB security may use the filed generic PN
    # result when period FCA evidence proves that exact subclass assignment.
    # Its filed EPS yields a security-equivalent weighted denominator for TTM;
    # units and exact-but-unequal multi-class disclosures remain on the existing
    # strict-null path.
    if len(components) != 1 or components[0].quantity != 1 or basic_values is None:
        return None
    target = components[0].per_share_class
    mapped_basic = _generic_preferred_value(basic_values, target, period_classes)
    if mapped_basic is None:
        return None
    mapped_diluted = None
    if diluted_values is not None:
        mapped_candidate = _generic_preferred_value(
            diluted_values, target, period_classes
        )
        if mapped_candidate == mapped_basic:
            mapped_diluted = mapped_candidate

    return Cpc41Disclosure(
        basic_base_eps=mapped_basic,
        diluted_base_eps=mapped_diluted,
        security_multiplier=Decimal(1),
    )


def standardize(
    by_module: Mapping[str, Any],
    sector: Sector,
    reference_date: date,
    *,
    per_share_components: Sequence[UnitComponent] = (),
    per_share_accounts: Accounts | None = None,
    per_share_classes: Sequence[PerShareClass] = (),
    period_share_classes: Sequence[PerShareClass] = (),
    per_share_rights_reason: NullReason = NullReason.MISSING_ECONOMIC_RIGHTS,
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
    cpc41_accounts = dre if per_share_accounts is None else per_share_accounts
    eps_basic, eps_basic_reason = _filed_per_share(
        cpc41_accounts,
        "3.99.01",
        per_share_components,
        period_classes=period_share_classes,
        missing_rights_reason=per_share_rights_reason,
    )
    eps_diluted, eps_diluted_reason = _filed_per_share(
        cpc41_accounts,
        "3.99.02",
        per_share_components,
        period_classes=period_share_classes,
        missing_rights_reason=per_share_rights_reason,
    )
    cpc41_disclosure = _reconciled_cpc41(
        cpc41_accounts,
        per_share_components,
        per_share_classes,
        period_share_classes,
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
        cpc41=cpc41_disclosure,
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
    dep_resolution = (
        _resolve_dep_amort(by_module, reference_date)
        if regime is AccountingRegime.CORPORATE
        else None
    )
    if regime is AccountingRegime.BANK:
        result = _as_bank(base, bpa, bpa_s, dre, dre_s)
    elif regime is AccountingRegime.INSURANCE:
        result = _as_insurer(base, bpa, bpa_s, bpp, bpp_s, dre, dre_s)
    else:
        assert dep_resolution is not None
        result = _as_corporate(
            base, bpa, bpa_s, bpp, bpp_s, dre, dre_s, dep_resolution.value
        )
    bank_inputs, bank_sources = (
        _bank_statement_roots(by_module, reference_date)
        if regime is AccountingRegime.BANK
        else (None, ())
    )
    return replace(
        result,
        bank_statement_inputs=bank_inputs,
        source_account_evidence=bank_sources
        + _source_account_evidence(
            by_module,
            result,
            per_share_accounts=cpc41_accounts,
            per_share_components=per_share_components,
            period_share_classes=period_share_classes,
            dep_amort_resolution=dep_resolution,
        ),
    )


def _bank_statement_roots(
    by_module: Mapping[str, Any], reference_date: date
) -> tuple[BankStatementInputs, tuple[SourceAccountEvidence, ...]]:
    """Select complete CVM bank perimeters without managerial substitutes."""
    dre = _accounts_of(by_module.get("DRE"))
    bpa = _accounts_of(by_module.get("BPA"))
    ds, bs = _scale(by_module, "DRE"), _scale(by_module, "BPA")
    sources: list[SourceAccountEvidence] = []

    def unique(accounts: Accounts) -> dict[str, Mapping[str, Any]] | None:
        selected: dict[str, Mapping[str, Any]] = {}
        for a in accounts:
            code = str(a.get("code", ""))
            if code in selected and (
                _finite_account_value(a) != _finite_account_value(selected[code])
                or _fold(str(a.get("name", "")))
                != _fold(str(selected[code].get("name", "")))
            ):
                return None
            selected[code] = a
        return selected

    def complete(parent: str) -> list[Mapping[str, Any]] | None:
        if d is None or parent not in d:
            return None
        children = [
            a
            for c, a in d.items()
            if c.startswith(parent + ".") and c.count(".") == parent.count(".") + 1
        ]
        values = [_finite_account_value(a) for a in children]
        total = _finite_account_value(d[parent])
        if not children or total is None or any(v is None for v in values):
            return None
        return (
            children
            if sum((v for v in values if v is not None), Decimal(0)) == total
            else None
        )

    def loss(a: Mapping[str, Any]) -> bool:
        n = _fold(str(a.get("name", "")))
        return any(w in n for w in ("provis", "perda", "impairment")) and any(
            w in n
            for w in ("credito", "emprest", "clientes", "financeir", "arrendamento")
        )

    def add(
        root: str,
        statement: str,
        rows: Sequence[Mapping[str, Any]],
        value: Decimal | None,
        formula: str,
    ) -> None:
        scale = ds if statement == "DRE" else bs
        sources.append(
            _source_entry(
                "bank_" + root,
                statement,
                _statement_period_evidence(by_module, statement),
                tuple(
                    SourceAccountRef(
                        str(a.get("code", "")),
                        str(a.get("name", "")),
                        _mul(_finite_account_value(a), scale),
                    )
                    for a in rows
                ),
                value,
                frozenset(),
                formula=formula,
            )
        )

    d, b = unique(dre), unique(bpa)
    revenue_rows, funding_rows, other_rows = (
        complete(c) for c in ("3.01", "3.02", "3.04")
    )
    net_interest = credit_loss = operating_income = operating_expenses = None
    interest_rows = [
        a
        for a in dre
        if str(a.get("code", "")).startswith(("3.01.", "3.02."))
        and str(a.get("code", "")).count(".") == 2
        and "juros" in _fold(str(a.get("name", "")))
    ]
    # Explicit interest totals, not gross intermediation/trading income.
    incoming = [a for a in interest_rows if str(a["code"]).startswith("3.01.")]
    outgoing = [a for a in interest_rows if str(a["code"]).startswith("3.02.")]
    if d is not None and len(incoming) == len(outgoing) == 1:
        vals = [_finite_account_value(a) for a in (incoming[0], outgoing[0])]
        if (
            vals[0] is not None
            and vals[1] is not None
            and vals[0] >= 0
            and vals[1] <= 0
        ):
            net_interest = (vals[0] + vals[1]) * ds
    add(
        "net_interest",
        "DRE",
        interest_rows,
        net_interest,
        "filed interest income + signed interest expense",
    )
    loss_rows = [
        a
        for a in dre
        if str(a.get("code", "")).count(".") == 2
        and str(a.get("code", "")).startswith(("3.02.", "3.04."))
        and loss(a)
    ]
    loan_losses: list[Mapping[str, Any]] = []
    credit_perimeter: str | None = None

    def credit_components(a: Mapping[str, Any]) -> bool:
        n = _fold(str(a.get("name", "")))
        if any(
            w in n for w in ("outros ativos", "demais ativos", "garantias", "titulos")
        ):
            return _finite_account_value(a) is not None
        if any(
            w in n
            for w in (
                "operacoes de credito",
                "emprest",
                "clientes",
                "creditos de liquidacao",
            )
        ):
            loan_losses.append(a)
            return _finite_account_value(a) is not None
        detail = complete(str(a.get("code", "")))
        if detail is not None:
            return all(credit_components(x) for x in detail)
        return _finite_account_value(a) == 0

    # Broad credit-risk losses include securities/guarantees. Reconciled details
    # can isolate customer loans, including leases only on a matching perimeter.
    if d is not None and all(credit_components(a) for a in loss_rows) and loan_losses:
        credit_perimeter = (
            "customer_loans_and_leases"
            if any("arrendamento" in _fold(str(a.get("name", ""))) for a in loan_losses)
            else "customer_loans"
        )
        credit_loss = (
            -sum(
                (cast(Decimal, _finite_account_value(a)) for a in loan_losses),
                Decimal(0),
            )
            * ds
        )
    loss_evidence = [
        a
        for a in dre
        if any(
            str(a.get("code", "")) == str(root.get("code", ""))
            or str(a.get("code", "")).startswith(str(root.get("code", "")) + ".")
            for root in loss_rows
        )
    ]
    add(
        "credit_loss",
        "DRE",
        loss_evidence,
        credit_loss,
        "-sum("
        + ",".join(str(a["code"]) for a in loan_losses)
        + "); "
        + str(credit_perimeter),
    )
    income_rows: list[Mapping[str, Any]] = []
    expense_rows: list[Mapping[str, Any]] = []
    if revenue_rows is not None and funding_rows is not None and other_rows is not None:

        def split_other(a: Mapping[str, Any], inherited: str | None = None) -> bool:
            if loss(a):
                return True
            n = _fold(str(a.get("name", "")))
            role = inherited
            if "despesa" in n and "receita" not in n:
                role = "expense"
            elif ("receita" in n and "despesa" not in n) or "equivalencia" in n:
                role = "income"
            prefix = str(a.get("code", "")) + "."
            nested_loss = any(
                str(x.get("code", "")).startswith(prefix)
                and loss(x)
                and _finite_account_value(x) != 0
                for x in dre
            )
            if nested_loss or role is None:
                detail = complete(str(a.get("code", "")))
                if detail is not None:
                    return all(split_other(x, role) for x in detail)
                if nested_loss:
                    return False
            if role == "expense":
                expense_rows.append(a)
            elif role == "income":
                income_rows.append(a)
            elif _finite_account_value(a) != 0:
                return False
            return True

        known = all(split_other(a) for a in other_rows)
        intermediation = _finite_account_value(d["3.03"]) if d and "3.03" in d else None
        totals = [_finite_account_value(d[c]) for c in ("3.01", "3.02")] if d else []
        if (
            known
            and intermediation is not None
            and all(v is not None for v in totals)
            and sum((v for v in totals if v is not None), Decimal(0)) == intermediation
        ):
            removed = sum(
                (
                    cast(Decimal, _finite_account_value(a))
                    for a in funding_rows
                    if loss(a)
                ),
                Decimal(0),
            )
            operating_income = (
                intermediation
                - removed
                + sum(
                    (cast(Decimal, _finite_account_value(a)) for a in income_rows),
                    Decimal(0),
                )
            ) * ds
            operating_expenses = (
                -sum(
                    (cast(Decimal, _finite_account_value(a)) for a in expense_rows),
                    Decimal(0),
                )
                * ds
            )
    full_dre = [
        a
        for a in dre
        if str(a.get("code", "")).startswith(("3.01", "3.02", "3.03", "3.04"))
    ]
    add(
        "operating_income",
        "DRE",
        full_dre,
        operating_income,
        "sum(3.03) - sum("
        + ",".join(str(a["code"]) for a in (funding_rows or []) if loss(a))
        + ") + sum("
        + ",".join(str(a["code"]) for a in income_rows)
        + ")",
    )
    add(
        "operating_expenses",
        "DRE",
        full_dre,
        operating_expenses,
        "-sum(" + ",".join(str(a["code"]) for a in expense_rows) + ")",
    )
    gross_credit = earning_assets = None
    gross_rows = [
        a
        for a in bpa
        if "brut" in _fold(str(a.get("name", "")))
        and any(
            w in _fold(str(a.get("name", "")))
            for w in (
                "carteira de credito",
                "operacoes de credito",
                "emprestimos a clientes",
            )
        )
    ]
    # A standalone gross total is conclusive. A loan amount accompanied by an
    # explicitly deducted nonzero provision is also gross; an absent/zero
    # provision cannot rule out an already-net loan disclosure.
    candidates = gross_rows[:]
    if b is not None and not candidates:
        for c, a in b.items():
            n = _fold(str(a.get("name", "")))
            if "operacoes de credito" not in n or any(
                w in n for w in ("provis", "liquid", "outros")
            ):
                continue
            parent = c.rsplit(".", 1)[0]
            allowances = [
                x
                for k, x in b.items()
                if k.startswith(parent + ".")
                and k.count(".") == c.count(".")
                and loss(x)
                and "arrendamento" not in _fold(str(x.get("name", "")))
            ]
            if len(allowances) == 1 and (
                _finite_account_value(allowances[0]) is not None
                and cast(Decimal, _finite_account_value(allowances[0])) < 0
            ):
                candidates.append(a)
                gross_rows.extend([a, *allowances])
    gross_perimeter: str | None = None
    gross_with_leases = None
    loan_rows = gross_rows[:]
    combined_codes: list[str] = []
    if b is not None and len(candidates) == 1:
        value = _finite_account_value(candidates[0])
        if value is not None and value >= 0:
            gross_credit = value * bs
            gross_perimeter = (
                "customer_loans_and_leases"
                if "arrendamento" in _fold(str(candidates[0].get("name", "")))
                else "customer_loans"
            )
            parent = str(candidates[0]["code"]).rsplit(".", 1)[0]
            leases = [
                a
                for c, a in b.items()
                if c.startswith(parent + ".")
                and c.count(".") == str(candidates[0]["code"]).count(".")
                and "operacoes de arrendamento" in _fold(str(a.get("name", "")))
                and not loss(a)
            ]
            allowances = [
                a
                for c, a in b.items()
                if c.startswith(parent + ".")
                and c.count(".") == str(candidates[0]["code"]).count(".")
                and "arrendamento" in _fold(str(a.get("name", "")))
                and loss(a)
            ]
            if (
                gross_perimeter == "customer_loans"
                and len(leases) == len(allowances) == 1
            ):
                lv, pv = (_finite_account_value(a) for a in (leases[0], allowances[0]))
                if (
                    lv is not None
                    and pv is not None
                    and ((lv == pv == 0) or (lv > 0 and -lv <= pv < 0))
                ):
                    gross_with_leases = gross_credit + lv * bs
                    combined_codes = [
                        str(candidates[0]["code"]),
                        str(leases[0]["code"]),
                    ]
                    gross_rows.extend([*leases, *allowances])
    if gross_perimeter == "customer_loans_and_leases":
        gross_with_leases = gross_credit
        combined_codes = [str(candidates[0]["code"])]
    add(
        "gross_credit_with_leases",
        "BPA",
        gross_rows,
        gross_with_leases,
        "sum(" + ",".join(combined_codes) + ")",
    )
    add(
        "gross_credit",
        "BPA",
        loan_rows,
        gross_credit,
        "sum("
        + ",".join(str(a["code"]) for a in candidates)
        + "); "
        + str(gross_perimeter),
    )
    earning_rows = [
        a
        for a in bpa
        if _fold(str(a.get("name", ""))).strip()
        in {
            "ativos remunerados",
            "ativos rentaveis",
            "ativos geradores de juros",
            "total dos ativos remunerados",
            "ativos financeiros remunerados",
        }
    ]
    if b is not None and len(earning_rows) == 1:
        value = _finite_account_value(earning_rows[0])
        if value is not None and value >= 0:
            earning_assets = value * bs
    add(
        "earning_assets",
        "BPA",
        earning_rows,
        earning_assets,
        "explicit complete interest-earning assets, not total/financial assets",
    )
    dp, bp = by_module.get("DRE", {}), by_module.get("BPA", {})
    dp = dp if isinstance(dp, Mapping) else {}
    bp = bp if isinstance(bp, Mapping) else {}
    inputs = BankStatementInputs(
        issuer=str(dp["cvm_code"]) if dp.get("cvm_code") else None,
        currency=(
            str(dp["currency"])
            if dp.get("currency") and dp.get("currency_size") in {1, 1000}
            else None
        ),
        bpa_issuer=str(bp["cvm_code"]) if bp.get("cvm_code") else None,
        bpa_currency=(
            str(bp["currency"])
            if bp.get("currency") and bp.get("currency_size") in {1, 1000}
            else None
        ),
        dre_scope=dp.get("balance_type"),
        bpa_scope=bp.get("balance_type"),
        period_start=_iso_date(dp.get("period_start_date")),
        period_end=_iso_date(dp.get("period_end_date")),
        balance_end=_iso_date(bp.get("period_end_date")),
        net_interest=net_interest,
        earning_assets=earning_assets,
        credit_loss=credit_loss,
        credit_loss_perimeter=credit_perimeter,
        gross_credit=gross_credit,
        gross_credit_perimeter=gross_perimeter,
        gross_credit_with_leases=gross_with_leases,
        operating_expenses=operating_expenses,
        operating_income=operating_income,
    )
    return inputs, tuple(sources)


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
    bank ratio resolver separately requires complete same-concept flows and
    compatible dated stock pairs. Partial components and closing-only balances
    do not establish those inputs.

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
    evidence = DebtCoverageEvidence(
        regime=AccountingRegime.BANK,
        regime_source=base.regime_source,
        primary_blocker=DebtBlocker.INAPPLICABLE_REGIME,
    )
    return replace(
        base,
        ebit=None,  # 3.05 is pre-tax profit, never EBIT (ADR 0058)
        # A bank files the CPC 03-labelled total directly at 1.01 and has no
        # current/non-current split from which to isolate broader investments.
        cash_equivalents=_cash_equivalents(bpa, bpa_s, AccountingRegime.BANK),
        loan_loss_provision=_mul(_child_by_name(dre, "3.02", "provisao"), dre_s),
        fee_income=_mul(_child_by_name(dre, "3.04", "prestacao de servicos"), dre_s),
        personnel_expense=_mul(_child_by_name(dre, "3.04", "pessoal"), dre_s),
        admin_expense=_mul(_child_by_name(dre, "3.04", "administrativas"), dre_s),
        loan_book=_loan_book(bpa, bpa_s),
        bank_ratio_null_reason=NullReason.MISSING_REGULATORY_DISCLOSURE,
        unmapped_fields=_BANK_UNMAPPED_FIELDS,
        debt_evidence=evidence,
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


def _insurance_underwriting_evidence(
    dre: Accounts, scale: Decimal
) -> InsuranceUnderwritingEvidence:
    """Prove insurance activity from the filing's complete DRE aggregates.

    The legacy component leaves are required to calculate the ratios, but their
    absence alone cannot distinguish a non-underwriting holding from an IFRS 17
    insurer whose filing only exposes broad aggregates. The top-level 3.01 and
    3.02 rows are the explicit consolidated perimeter: two parsed zeros prove
    zero underwriting activity; any other complete pair proves that activity
    exists; an absent or unreadable side leaves the status unknown.
    """
    revenue_account = _account_by_code(dre, "3.01")
    expense_account = _account_by_code(dre, "3.02")
    revenue_value = (
        None
        if revenue_account is None
        else _mul(_dec(revenue_account.get("quantity")), scale)
    )
    expense_value = (
        None
        if expense_account is None
        else _mul(_dec(expense_account.get("quantity")), scale)
    )
    if revenue_value is None or expense_value is None:
        status = InsuranceUnderwritingStatus.UNKNOWN
    elif revenue_value == 0 and expense_value == 0:
        status = InsuranceUnderwritingStatus.ZERO_ACTIVITY
    else:
        status = InsuranceUnderwritingStatus.ACTIVE

    return InsuranceUnderwritingEvidence(
        status=status,
        revenue_aggregate=(
            None
            if revenue_account is None
            else SourceAccountRef(
                code="3.01",
                name=str(revenue_account.get("name", "")),
                value=revenue_value,
            )
        ),
        expense_aggregate=(
            None
            if expense_account is None
            else SourceAccountRef(
                code="3.02",
                name=str(expense_account.get("name", "")),
                value=expense_value,
            )
        ),
    )


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
    3.07 rather than 3.05 — the insurer DRE carries an extra level. Debt is read
    from explicit labels, never from the corporate fixed codes: 2.01.04 is
    "Capitalização" in this chart. If both maturity aggregates are not evidenced,
    the paired cause names incomplete coverage instead of inventing zero.
    """
    assessment = _total_debt(bpp, bpp_s)
    total_debt = assessment.total_debt
    debt_reason = assessment.null_reason
    underwriting_evidence = _insurance_underwriting_evidence(dre, dre_s)
    # Through 2022 the structured insurer DRE separates earned premiums, claims
    # and acquisition costs under the insurance and reinsurance branches. IFRS 17
    # replaced those leaves with broader service/reinsurance aggregates in 2023.
    # Those aggregates do not preserve the components required by the loss and
    # combined ratios, so absence of the legacy leaves stays a named null (ADR 0061).
    earned_premium = _sum(
        _by_code(dre, "3.01.01.01"),
        _by_code(dre, "3.01.02.01"),
    )
    claims_incurred = _sum(
        _by_code(dre, "3.02.01.01"),
        _by_code(dre, "3.02.02.01"),
    )
    acquisition_costs = _sum(
        _by_code(dre, "3.02.01.02"),
        _by_code(dre, "3.02.02.02"),
    )
    return replace(
        base,
        ebit=_mul(_by_code(dre, "3.07"), dre_s),  # before financial result/taxes
        cash_equivalents=_cash_equivalents(bpa, bpa_s, AccountingRegime.CORPORATE),
        current_financial_investments=_mul(_by_code(bpa, "1.01.02"), bpa_s),
        current_assets=_mul(_by_code(bpa, "1.01"), bpa_s),
        current_liabilities=_mul(_by_code(bpp, "2.01"), bpp_s),
        total_debt=total_debt,
        debt_coverage_null_reason=debt_reason,
        debt_evidence=_debt_evidence(base, AccountingRegime.INSURANCE, assessment),
        earned_premium=_mul(earned_premium, dre_s),
        claims_incurred=_mul(claims_incurred, dre_s),
        acquisition_costs=_mul(acquisition_costs, dre_s),
        insurance_admin_expenses=_mul(_by_code(dre, "3.04"), dre_s),
        insurance_underwriting_evidence=underwriting_evidence,
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
    dep_amort: Decimal | None,
) -> StandardizedFinancials:
    """The standard chart of accounts — and what CXSE3 files, despite its sector."""
    ebit = _mul(_by_code(dre, "3.05"), dre_s)  # before financial result/taxes
    assessment = _total_debt(bpp, bpp_s)
    total_debt = assessment.total_debt
    debt_reason = assessment.null_reason
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
        cash_equivalents=_cash_equivalents(bpa, bpa_s, AccountingRegime.CORPORATE),
        current_financial_investments=_mul(_by_code(bpa, "1.01.02"), bpa_s),
        current_assets=_mul(_by_code(bpa, "1.01"), bpa_s),
        current_liabilities=_mul(_by_code(bpp, "2.01"), bpp_s),
        total_debt=total_debt,
        debt_coverage_null_reason=debt_reason,
        debt_evidence=_debt_evidence(base, AccountingRegime.CORPORATE, assessment),
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
        issuer_resolver: IssuerResolver = _no_issuer,
        per_share_resolver: PerShareResolver = _no_per_share_components,
        per_share_classes_resolver: PerShareClassesResolver = _no_per_share_classes,
        period_share_classes_resolver: PeriodShareClassesResolver = (
            no_period_share_classes
        ),
        per_share_rights_reason_resolver: PerShareRightsReasonResolver = (
            _missing_economic_rights
        ),
    ) -> None:
        self._collection = collection
        # The sector only seeds the ``expected_regime`` fallback (the filed regime,
        # read off the statement, decides applicability). The CLI injects a
        # registry-backed resolver, unconditionally (#212).
        self._sector_resolver = sector_resolver
        # Which registrant's filings to read (ADR 0030) — the same resolution, from
        # the same registry, that the sector one uses.
        self._registrant = registrant_resolver
        self._issuer = issuer_resolver
        self._per_share = per_share_resolver
        self._per_share_classes = per_share_classes_resolver
        self._period_share_classes = period_share_classes_resolver
        self._per_share_rights_reason = per_share_rights_reason_resolver

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
        company_classes = self._per_share_classes(ticker)
        rights_reason = self._per_share_rights_reason(ticker)
        identity = self._issuer(ticker)
        period_cnpj = (identity.cnpj or "") if identity is not None else ""

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
                per_share_year = int(str(per_share_ref)[:4])
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
                basic, _basic_reason = _filed_per_share(
                    accounts,
                    "3.99.01",
                    components,
                    period_classes=self._period_share_classes(
                        period_cnpj, per_share_year
                    ),
                    missing_rights_reason=rights_reason,
                )
                diluted, _diluted_reason = _filed_per_share(
                    accounts,
                    "3.99.02",
                    components,
                    period_classes=self._period_share_classes(
                        period_cnpj, per_share_year
                    ),
                    missing_rights_reason=rights_reason,
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
                if module in {"DRE", "DVA"}
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

        fallback_cd_cvm = self._registrant(ticker)
        loaded: list[tuple[str | None, StandardizedFinancials]] = []
        for ref, modules in sorted(by_period.items()):
            if modules.keys() == {"DVA"}:
                continue
            payloads = [
                payload for payload in modules.values() if isinstance(payload, Mapping)
            ]
            dre_payload = modules.get("DRE")
            raw_cd_cvm = next(
                (
                    str(payload.get("cvm_code"))
                    for payload in payloads
                    if payload.get("cvm_code") is not None
                ),
                None,
            )
            raw_issuer_name = next(
                (
                    str(payload.get("company_name"))
                    for payload in (
                        [dre_payload] if isinstance(dre_payload, Mapping) else []
                    )
                    if payload.get("company_name")
                ),
                next(
                    (
                        str(payload.get("company_name"))
                        for payload in payloads
                        if payload.get("company_name")
                    ),
                    None,
                ),
            )
            cd_cvm = (identity.cd_cvm if identity is not None else None) or raw_cd_cvm
            cd_cvm = cd_cvm or fallback_cd_cvm
            issuer_name = (
                identity.issuer_name if identity is not None else None
            ) or raw_issuer_name
            cnpj = identity.cnpj if identity is not None else None
            financials = standardize(
                modules,
                sector,
                date.fromisoformat(ref),
                per_share_components=components,
                per_share_accounts=_accounts_of(
                    per_share_by_period.get(ref, modules.get("DRE"))
                ),
                per_share_classes=company_classes,
                period_share_classes=self._period_share_classes(
                    cnpj or "", date.fromisoformat(ref).year
                ),
            )
            evidence = financials.debt_evidence
            if evidence is not None:
                identity_status = (
                    DebtIdentityStatus.RESOLVED
                    if cd_cvm and cnpj and issuer_name
                    else DebtIdentityStatus.UNKNOWN
                )
                evidence = replace(evidence, identity_status=identity_status)
            loaded.append(
                (
                    doc_type.get(ref),
                    replace(
                        financials,
                        issuer_name=issuer_name,
                        cd_cvm=cd_cvm,
                        cnpj=cnpj,
                        debt_evidence=evidence,
                    ),
                )
            )
        history = [p for _, p in loaded]
        return [(tag, resolve_bank_ratios(f, history)) for tag, f in loaded]


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
