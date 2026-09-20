"""Source: NBG's account statement export.

The export is XLSX with two sheets, `data` then `info`; convert the first to CSV
before parsing (LibreOffice: `soffice --headless --convert-to csv
statement.xlsx`, which converts the first sheet only).

Only cleared transactions appear in this export -- the bank does not publish
pending items here. That is the one real limitation of the CSV route, and the
reason the PSD2 API is worth adding later.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from ..model import Transaction, derive_fallback_id, parse_greek_decimal

# Column names as they appear in the export. NBG renamed these from Greek to
# English in September 2026. Only the header row changed: the values are still
# Greek (Χ/Π direction markers, Greek payee names), and the amount and date
# formats were unaffected.
COL_SERIAL = "Transaction No."
COL_POSTED = "Date"
COL_VALEUR = "Valeur"
COL_TYPE = "Transaction type"
COL_TXN_AMOUNT = "Transaction amount"
COL_ORDER_AMOUNT = "Order amount"
COL_DR_CR = "Debit / Credit"
COL_DESCRIPTION = "Description"
COL_COUNTERPARTY = "Counterparty name"
COL_EXTRA = "Additional information"
COL_REFERENCE = "Reference number"
COL_MERCHANT = "Merchant / Organization code"

COLUMN_DATE_FORMATS = {
    COL_VALEUR: "%m/%d/%y",
    COL_POSTED: "%d/%m/%Y",
}
ENCODINGS = ("utf-8-sig", "cp1253", "iso-8859-7")

# Values of Debit / Credit that mean money left the account. The export emits
# the Greek markers (Χ/Π) even under the English column names; the Latin ones
# are kept as a hedge in case the values get localised too.
DEBIT_MARKERS = {"Χ", "ΧΡΕΩΣΗ", "D", "DR", "DEBIT"}


class ParseError(ValueError):
    """Raised with enough context to find the offending row in the file."""


def read(path: Path | str, *, encoding: str | None = None) -> list[Transaction]:
    """Parse an NBG CSV export into normalised transactions."""
    path = Path(path)
    text = _read_text(path, encoding)
    reader = csv.DictReader(text.splitlines())

    if reader.fieldnames is None:
        raise ParseError(f"{path}: no header row")
    _assert_columns(reader.fieldnames, path)

    return list(_rows(reader, path))


def _rows(reader: csv.DictReader, path: Path) -> Iterator[Transaction]:
    for line_no, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue  # trailing blank line
        try:
            yield _to_transaction(row)
        except (ValueError, InvalidOperation) as exc:
            raise ParseError(f"{path}:{line_no}: {exc}") from exc


def _to_transaction(row: dict[str, str]) -> Transaction:
    return Transaction(
        source_id=_source_id(row),
        date=_date(row),
        amount=_amount(row),
        payee=_payee(row),
        memo=_memo(row),
        cleared=True,  # everything in this export has already settled
    )


def _source_id(row: dict[str, str]) -> str:
    """Stable id that survives re-exports with different serial numbers.

    The bank's reference number is stable, but fee rows share the same
    reference as the parent transaction. We disambiguate with the
    description, which differs between fees and the main transaction.
    The serial number is NOT used — it shifts when the export covers a
    different date range.
    """
    ref = (row.get(COL_REFERENCE) or "").strip()
    desc = (row.get(COL_DESCRIPTION) or "").strip()
    amt = (row.get(COL_ORDER_AMOUNT) or row.get(COL_TXN_AMOUNT) or "").strip()
    if ref:
        return f"{ref}:{desc}:{amt}"
    return derive_fallback_id(
        row.get(COL_POSTED, ""),
        row.get(COL_VALEUR, ""),
        amt,
        desc,
    )


def _date(row: dict[str, str]) -> date:
    """Posting date first -- it matches the order the bank shows transactions,
    which makes it easier to compare YNAB against the bank statement when
    working through the approval queue. Valeur is the fallback.
    """
    for column in (COL_POSTED, COL_VALEUR):
        raw = (row.get(column) or "").strip()
        if not raw:
            continue
        fmt = COLUMN_DATE_FORMATS[column]
        raw = raw.split(" ")[0].strip()
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"no usable date in {COL_VALEUR} or {COL_POSTED}")


def _amount(row: dict[str, str]) -> Decimal:
    """Signed order amount, with the debit/credit column as a safety net."""
    raw = (row.get(COL_ORDER_AMOUNT) or "").strip()
    if not raw:
        raw = (row.get(COL_TXN_AMOUNT) or "").strip()
    if not raw:
        raise ValueError(f"no amount in {COL_ORDER_AMOUNT} or {COL_TXN_AMOUNT}")

    amount = parse_greek_decimal(raw)

    # Some exports write the amount unsigned and carry the direction in a
    # separate column. Only apply it when the amount is genuinely unsigned.
    if amount > 0 and not raw.lstrip().startswith(("-", "(")):
        marker = (row.get(COL_DR_CR) or "").strip().upper()
        if marker in DEBIT_MARKERS:
            amount = -amount

    return amount


def _payee(row: dict[str, str]) -> str | None:
    """Counterparty name is the good field; description is the fallback.

    Whatever lands here is put through payees.py before being sent, and then
    through YNAB's own rename rules on arrival -- the latter only because we
    send an import_id. That is another reason to push via the API rather than
    file import.
    """
    for column in (COL_COUNTERPARTY, COL_DESCRIPTION, COL_MERCHANT, COL_TYPE):
        value = (row.get(column) or "").strip()
        if value:
            return value
    return None


def _memo(row: dict[str, str]) -> str | None:
    """Keep the raw bank text so a bad payee mapping is always recoverable."""
    parts = [
        (row.get(COL_DESCRIPTION) or "").strip(),
        (row.get(COL_EXTRA) or "").strip(),
    ]
    # Don't repeat whatever we already used as the payee.
    payee = _payee(row)
    parts = [p for p in parts if p and p != payee]
    return " | ".join(dict.fromkeys(parts)) or None


def _read_text(path: Path, encoding: str | None) -> str:
    candidates = (encoding,) if encoding else ENCODINGS
    last: Exception | None = None
    for enc in candidates:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError) as exc:
            last = exc
    raise ParseError(
        f"{path}: could not decode (tried {', '.join(candidates)}); "
        f"pass --encoding explicitly"
    ) from last


def _assert_columns(fieldnames: list[str], path: Path) -> None:
    present = {f.strip() for f in fieldnames}
    required = {COL_POSTED, COL_VALEUR}
    missing = required - present
    if missing:
        raise ParseError(
            f"{path}: missing expected column(s): {', '.join(sorted(missing))}. "
            f"Found: {', '.join(sorted(present))}"
        )
    if not present & {COL_ORDER_AMOUNT, COL_TXN_AMOUNT}:
        raise ParseError(
            f"{path}: no amount column ({COL_ORDER_AMOUNT} or {COL_TXN_AMOUNT})"
        )
