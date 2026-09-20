"""The normalised transaction shape that every source produces.

This is the seam. A source (CSV today, NBG PSD2 API later) is anything that
yields `Transaction` objects; the YNAB push side never sees source-specific
fields.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

# YNAB caps import_id at 36 characters.
IMPORT_ID_MAX = 36
# YNAB truncates beyond these; we do it ourselves so the value is guaranteed
PAYEE_MAX = 50
MEMO_MAX = 200

IMPORT_ID_PREFIX = "NBG"


@dataclass(frozen=True)
class Transaction:
    """One bank transaction, source-agnostic.

    Attributes:
        source_id:  Stable identifier from the source system. MUST be stable
                    across exports for the same underlying transaction.
        date:       The date to show in YNAB. For NBG this is the posting
                    date (`Date`), falling back to `Valeur`. It matches the
                    order the bank lists transactions in.
        amount:     Signed, in currency units. Negative = outflow.
        payee:      Counterparty name, or a fallback description.
        memo:       Free text; useful for keeping the raw bank description.
        cleared:    Whether the bank considers this settled.
    """

    source_id: str
    date: date
    amount: Decimal
    payee: str | None = None
    memo: str | None = None
    cleared: bool = True

    @property
    def milliunits(self) -> int:
        """YNAB stores money as thousandths of a currency unit."""
        return int(
            (self.amount * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

    @property
    def import_id(self) -> str:
        """Deterministic, idempotent key for this transaction.

        YNAB refuses to create a second transaction with an import_id it has
        already seen on the same account, so this is what makes re-running the
        importer over an overlapping date range a no-op.

        It also makes YNAB attempt to match this against an existing
        user-entered transaction on the same account with the same amount and
        a date within +/-10 days; this is how manually entered transactions
        get merged rather than duplicated.

        Derived purely from source_id: a hash keeps us inside the 36-char
        limit regardless of how long or how Greek the bank's reference is.
        """
        digest = hashlib.sha256(self.source_id.encode("utf-8")).hexdigest()
        room = IMPORT_ID_MAX - len(IMPORT_ID_PREFIX) - 1
        return f"{IMPORT_ID_PREFIX}:{digest[:room]}"

    def to_ynab(
        self,
        account_id: str,
        *,
        approved: bool = False,
        flag_color: str | None = None,
    ) -> dict:
        payload = {
            "account_id": account_id,
            "date": self.date.isoformat(),
            "amount": self.milliunits,
            "payee_name": _clip(self.payee, PAYEE_MAX),
            "memo": _clip(self.memo, MEMO_MAX),
            "cleared": "cleared" if self.cleared else "uncleared",
            # Never auto-approve. The approval queue IS the workflow: it is
            # where categorising happens and where a bad match gets caught.
            "approved": approved,
            "import_id": self.import_id,
        }
        if flag_color:
            payload["flag_color"] = flag_color
        return payload


def _clip(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit] or None


def parse_greek_decimal(raw: str) -> Decimal:
    """Parse an NBG amount, whichever separator convention it arrives in.

    Handles '1.234,56', '-45,20', '(45,20)', '45.20', '1234.56' and '1234'.
    """
    if raw is None:
        raise ValueError("empty amount")
    text = raw.strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        raise ValueError("empty amount")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.startswith("-"):
        negative, text = True, text[1:]
    elif text.startswith("+"):
        text = text[1:]

    text = text.replace("€", "").strip()

    # Separator type cannot be guaranteed so we check by position
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    value = Decimal(text)
    return -value if negative else value


def derive_fallback_id(*parts: str) -> str:
    """Last-resort stable id when the bank gives us no reference number.

    Built from fields that do not change once a transaction has posted. Only
    used when the preferred reference columns are blank.
    """
    joined = "\u241f".join(p.strip() for p in parts)
    return "derived:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]
