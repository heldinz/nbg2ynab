"""Command line interface.

    nbg2ynab accounts
    nbg2ynab push statement.csv --dry-run   # parse only, no credentials
    nbg2ynab push statement.csv --preview   # read-only diff against YNAB
    nbg2ynab push statement.csv
    nbg2ynab check --since 2026-06-15

Configuration comes from the environment, or from a .env file next to this
package (a real environment variable wins over the file):

    YNAB_TOKEN       personal access token (Account Settings -> Developer)
    YNAB_PLAN_ID     the plan (formerly "budget") id
    YNAB_ACCOUNT_ID  the account transactions belong to
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .env import load_dotenv
from .model import Transaction
from .payees import rename as rename_payee
from .sources import nbg_csv
from .ynab import YnabClient, YnabError

FLAG_COLORS = ("red", "orange", "yellow", "green", "blue", "purple")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (nbg_csv.ParseError, YnabError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# ---- commands ---------------------------------------------------------


def cmd_accounts(args: argparse.Namespace) -> int:
    client = YnabClient(_env("YNAB_TOKEN"), _env("YNAB_PLAN_ID"))
    for account in client.list_accounts():
        marker = " (closed)" if account.get("closed") else ""
        print(f"{account['id']}  {account['name']}{marker}")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    transactions = nbg_csv.read(args.csv, encoding=args.encoding)
    transactions = _filter(transactions, args.since, args.until)

    if not transactions:
        print("nothing to push in that date range")
        return 0

    _warn_on_duplicate_ids(transactions)

    if args.dry_run:
        # No credentials needed to check the parse is right.
        _print_preview(transactions)
        print(
            f"\n{len(transactions)} transaction(s) would be sent. "
            f"No API call made."
        )
        return 0

    account_id = args.account or _env("YNAB_ACCOUNT_ID")

    if args.preview:
        return _preview_against_ynab(transactions, account_id)
    payloads = []
    for t in transactions:
        p = t.to_ynab(account_id, flag_color=args.flag)
        p["payee_name"] = rename_payee(p["payee_name"])
        payloads.append(p)

    client = YnabClient(_env("YNAB_TOKEN"), _env("YNAB_PLAN_ID"))
    result = client.create_transactions(payloads)

    print(f"created:      {len(result.created)}")
    print(f"already seen: {len(result.duplicates)}")
    print(
        "\nCreated transactions are unapproved. Open the account in YNAB and "
        "work the approval queue -- anything YNAB matched to an existing "
        "manual entry is merged there, not duplicated."
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Reconciliation report. Run this after a catch-up push.

    Two things worth knowing before trusting the pipeline:
      1. Are our imports landing (import_id stable, no drift)?
      2. Which old manual entries did NOT get matched, and so still need
         pairing by hand?
    """
    client = YnabClient(_env("YNAB_TOKEN"), _env("YNAB_PLAN_ID"))
    account_id = args.account or _env("YNAB_ACCOUNT_ID")
    existing = client.list_transactions(
        account_id, since=args.since, until=args.until
    )

    ours = [t for t in existing if (t.get("import_id") or "").startswith("NBG:")]
    manual = [t for t in existing if not t.get("import_id")]
    unapproved = [t for t in existing if not t.get("approved")]
    orphans = [t for t in manual if t.get("cleared") == "uncleared"]

    print(f"transactions in range:        {len(existing)}")
    print(f"  imported by this tool:      {len(ours)}")
    print(f"  user-entered (no import_id):{len(manual):>4}")
    print(f"  awaiting approval:          {len(unapproved)}")

    if orphans:
        print(
            f"\n{len(orphans)} uncleared manual entr(ies) with no import "
            f"attached. These are the ones the +/-10 day amount match missed "
            f"-- usually an estimated amount, a tip, or an FX difference. "
            f"Pair each with its import using Match in the YNAB web app, or "
            f"correct the amount and re-run push.\n"
        )
        for t in sorted(orphans, key=lambda t: t["date"]):
            amount = Decimal(t["amount"]) / 1000
            payee = t.get("payee_name") or "(no payee)"
            print(f"  {t['date']}  {amount:>10,.2f}  {payee}")
    else:
        print("\nno unmatched manual entries -- account is clean")

    return 0


def _preview_against_ynab(
    transactions: list[Transaction], account_id: str
) -> int:
    """Read-only comparison against existing YNAB transactions."""
    client = YnabClient(_env("YNAB_TOKEN"), _env("YNAB_PLAN_ID"))

    # Fetch a window wide enough to cover ±10 day matching.
    dates = [t.date for t in transactions]
    since = min(dates) - timedelta(days=15)
    until = max(dates) + timedelta(days=15)
    existing = client.list_transactions(
        account_id, since=since, until=until
    )

    # Index existing by import_id for duplicate detection.
    by_import_id = {
        t["import_id"]: t for t in existing if t.get("import_id")
    }
    # Manual entries (no import_id) available for merge matching.
    manual = [t for t in existing if not t.get("import_id")]

    duplicates, merges, new = [], [], []
    matched_manual: set[str] = set()

    for t in transactions:
        if t.import_id in by_import_id:
            duplicates.append(t)
            continue

        # Simulate YNAB's merge: same amount, date within ±10 days.
        merge_candidate = None
        for m in manual:
            if m["id"] in matched_manual:
                continue
            if m["amount"] != t.milliunits:
                continue
            m_date = date.fromisoformat(m["date"])
            if abs((t.date - m_date).days) <= 10:
                merge_candidate = m
                break

        if merge_candidate:
            merges.append((t, merge_candidate))
            matched_manual.add(merge_candidate["id"])
        else:
            new.append(t)

    print(f"already pushed (duplicate):  {len(duplicates)}")
    print(f"would merge with manual:     {len(merges)}")
    print(f"would create new:            {len(new)}")

    if merges:
        print(f"\n-- merges --")
        for t, m in merges:
            payee = rename_payee(t.payee) or "(no payee)"
            m_payee = m.get("payee_name") or "(no payee)"
            print(
                f"  {t.date}  {t.amount:>10,.2f}  {payee}"
                f"  <->  {m['date']}  {m_payee}"
            )

    if new:
        print(f"\n-- new --")
        for t in new:
            payee = rename_payee(t.payee) or "(no payee)"
            print(f"  {t.date}  {t.amount:>10,.2f}  {payee}")

    print(f"\nNo changes made. Run without --preview to push.")
    return 0


# ---- helpers ----------------------------------------------------------


class ConfigError(RuntimeError):
    pass


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def _filter(
    transactions: list[Transaction], since: date | None, until: date | None
) -> list[Transaction]:
    result = transactions
    if since:
        result = [t for t in result if t.date >= since]
    if until:
        result = [t for t in result if t.date <= until]
    return sorted(result, key=lambda t: (t.date, t.source_id))


def _warn_on_duplicate_ids(transactions: list[Transaction]) -> None:
    """A collision inside one file means the source_id choice is wrong.

    Better to hear about it now than to discover a missing transaction three
    months later during a reconcile.
    """
    seen: dict[str, Transaction] = {}
    for t in transactions:
        clash = seen.get(t.import_id)
        if clash:
            print(
                f"warning: {t.date} {t.amount} and {clash.date} {clash.amount} "
                f"share a source id ({t.source_id!r}). One will be dropped by "
                f"YNAB as a duplicate.",
                file=sys.stderr,
            )
        seen[t.import_id] = t


def _print_preview(transactions: list[Transaction]) -> None:
    for t in transactions:
        original = (t.payee or "(no payee)")[:34]
        renamed = rename_payee(t.payee)
        if renamed and renamed != t.payee:
            payee_col = f"{original:<34} -> {renamed}"
        else:
            payee_col = f"{original:<34}"
        print(f"{t.date}  {t.amount:>10,.2f}  {payee_col}")


def _date_arg(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {raw!r}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nbg2ynab", description="Push NBG transactions into YNAB."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    accounts = sub.add_parser("accounts", help="list account ids in the plan")
    accounts.set_defaults(handler=cmd_accounts)

    push = sub.add_parser("push", help="import a CSV export into YNAB")
    push.add_argument("csv", type=Path, help="NBG statement export as CSV")
    push.add_argument("--account", help="override YNAB_ACCOUNT_ID")
    push.add_argument("--since", type=_date_arg, help="skip rows before this date")
    push.add_argument("--until", type=_date_arg, help="skip rows after this date")
    push.add_argument("--encoding", help="force a CSV encoding")
    push.add_argument(
        "--flag", choices=FLAG_COLORS, help="flag imported transactions"
    )
    push.add_argument(
        "-n", "--dry-run", action="store_true", help="print, don't send"
    )
    push.add_argument(
        "--preview", action="store_true",
        help="compare against YNAB (read-only) and predict merges",
    )
    push.set_defaults(handler=cmd_push)

    check = sub.add_parser("check", help="report on import health and orphans")
    check.add_argument("--account", help="override YNAB_ACCOUNT_ID")
    check.add_argument("--since", type=_date_arg, help="start of range")
    check.add_argument("--until", type=_date_arg, help="end of range")
    check.set_defaults(handler=cmd_check)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
