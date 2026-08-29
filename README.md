# nbg2ynab

Pushes National Bank of Greece (NBG) statement exports into YNAB over the API, idempotently.

No dependencies beyond the Python 3.10+ standard library. Nothing leaves your machine except calls to `api.ynab.com`.

## Why the API rather than file import

Every transaction is sent with a deterministic `import_id` derived from the bank's own reference number. YNAB refuses to create a second transaction with an `import_id` it has already seen on that account, which buys two things:

1. **Re-running is a no-op.** Overlapping date ranges are free, so you never have to reason about "where did I get to last time".
2. **Manual entries get merged, not duplicated.** When an `import_id` is present, YNAB also tries to match the import against an existing user-entered transaction on the same account with the same amount and a date within ±10 days. That is what makes the catch-up below work.

YNAB's own payee rename rules also only fire on transactions that carry an `import_id`, so imports get your renames and hand-entered transactions don't. (Renaming happens locally too — see [Payee names](#payee-names).)

## Setup

The tool runs as a module (`python3 -m nbg2ynab`), which means the checkout has to be named `nbg2ynab`, and every command below is run from the directory *containing* it rather than from inside it:

```sh
git clone <repo-url> nbg2ynab
# stay in this directory -- don't cd into nbg2ynab/
```

There is nothing to install: Python 3.10 or newer is the only requirement.

Copy the two template files. Neither copy is tracked by git — they hold a token and a list of the people you pay, respectively:

```sh
cp nbg2ynab/.env.template nbg2ynab/.env
cp nbg2ynab/payees.json.template nbg2ynab/payees.json
```

### Filling in `.env`

Three variables, and you get them in this order — the third needs the first two to already be set:

1. `YNAB_TOKEN` — YNAB → Account Settings → Developer Settings → New Token.
2. `YNAB_PLAN_ID` — in the URL when you open the plan in the web app. ("Plan" is what the API now calls a budget.)
3. `YNAB_ACCOUNT_ID` — with the first two filled in, run `python3 -m nbg2ynab accounts` and copy the id of your NBG account.

The format is one `KEY=value` per line, `#` for comments. A real environment variable always wins over the file, so you can still override ad hoc:

```sh
YNAB_ACCOUNT_ID=... python3 -m nbg2ynab push statement.csv
```

### Filling in `payees.json`

The template is an empty `{}`, which is a valid starting point — every payee is title-cased and nothing more until you add rules. See [Payee names](#payee-names) for what to put in it; the easiest way to build it up is to run a `--dry-run` and add rules for whatever looks ugly.

## Getting a CSV

The bank exports XLSX. Convert it:

```sh
soffice --headless --convert-to csv statement.xlsx
```

Encoding is auto-detected (`utf-8-sig`, then `cp1253`, then `iso-8859-7`). Override with `--encoding` if it guesses wrong.

## Payee names

Payees are cleaned up locally, before anything is sent, using the `payees.json` you copied during setup. It maps regex patterns to replacements:

```json
{
  "APPLE\\.COM": "Apple",
  "VODAFONE": "Vodafone Greece",
  "\\.GR\\b": ""
}
```

Rules are tried in order, case-insensitively, and the first match wins:

- **Non-empty replacement** — replaces the whole payee.
- **Empty replacement** — strips only the matched text from the payee and title-cases what's left. Useful for shaving off `.COM`/`.GR` suffixes.
- **No rule matches** — the payee is title-cased as-is.

The file is optional. An empty `{}` — or no `payees.json` at all — means every payee is title-cased and nothing else. It stays out of git, since it tends to fill up with the names of people you pay.

Both `--dry-run` and `--preview` show the renamed payee, so you can iterate on the rules without touching your budget.

## Catching up

The export only contains cleared transactions, so go back further than you think you need — overlap costs nothing.

```sh
# 1. Check the parse before touching your budget. Needs no credentials.
python3 -m nbg2ynab push statement.csv --dry-run

# 2. Check what it will actually do to the account: what's already been
#    pushed, what YNAB will merge into a manual entry, what's genuinely new.
#    Read-only.
python3 -m nbg2ynab push statement.csv --preview

# 3. Push. Everything lands unapproved.
python3 -m nbg2ynab push statement.csv

# 4. Work the approval queue in YNAB. Matched transactions appear merged
#    with your old manual entries; the rest need categorising.

# 5. Find what the match missed.
python3 -m nbg2ynab check --since 2026-06-15
```

Step 2 simulates YNAB's ±10 day amount match locally, against the transactions actually in your account. It is a prediction, not a promise — YNAB does the real matching server-side — but it is where you find out that a catch-up you thought was 200 new transactions is really 190 merges.

Step 5 lists uncleared manual entries with no import attached. Those are the ones where the ±10 day amount match couldn't pair anything — usually because you entered an estimate, or a tip or FX difference moved the amount. Pair each one by hand using **Match** in the YNAB web app, or fix the amount on the manual entry and re-run `push` (which is safe).

Finish by reconciling the account against the bank balance.

## Day to day

```sh
python3 -m nbg2ynab push statement.csv
```

Weekly is about right. The job is now *review*, not entry.

## Commands

```text
accounts                       list account ids in the plan
push <csv> [options]           import a CSV export
check [options]                report on import health and orphans
```

`push` options:

| Option | Effect |
| --- | --- |
| `-n`, `--dry-run` | Print the parsed rows and stop. No API call, no credentials needed. Takes precedence over `--preview`. |
| `--preview` | Read-only comparison against YNAB: duplicates, predicted merges, genuinely new. |
| `--since`, `--until` | `YYYY-MM-DD`. Skip rows outside the range. |
| `--encoding` | Force a CSV encoding instead of auto-detecting. |
| `--flag` | Flag every imported transaction: `red`, `orange`, `yellow`, `green`, `blue`, `purple`. Handy for making a catch-up batch visible in the register. |
| `--account` | Override `YNAB_ACCOUNT_ID`. |

`check` takes `--account`, `--since` and `--until` with the same meanings.

## Adding the PSD2 API later

`sources/nbg_csv.py` is the only module that knows anything about NBG. A `sources/nbg_api.py` that returns the same `Transaction` objects drops in without touching `ynab.py`, `model.py`, or the payload logic. What that buys you over the CSV: pending transactions (which the export omits entirely) and no manual export step.

Two things to keep when you do:

- Use `bookingDateTime` for `Transaction.date`, falling back to `valueDateTime`, to match what the CSV source does — the posting date is the one that lines up with the order the bank shows transactions in, which is what you want while working the approval queue.
- Use `TransactionReference` alone for `source_id`, and set `cleared` from the booked/pending status. The reference is unique per entry over the API, which the CSV's isn't (see below) — so a transaction that goes Pending → Booked, changing its date, payee, or even its amount, is still recognised as the same transaction rather than imported twice. That property is worth protecting; don't fold anything mutable into `source_id`.

## The one thing that can bite you

Everything rests on `source_id` being stable across exports.

For the CSV source, `source_id` is the bank's reference number joined with the description and the order amount. The reference alone isn't enough: NBG gives a fee row the same reference as the transaction it belongs to, so keying on it alone would silently drop every fee as a duplicate.

The cost of that compromise is that the description and the amount are now load-bearing. If the bank restates either — or restates its reference numbers — you get a duplicate rather than a no-op. In practice the export is stable once a transaction has posted, but this is the assumption to check first when something looks wrong.

`push` warns on collisions within a single file, and `check` will show the counts drifting. Run `check` after the first few imports before you stop paying attention.

## Tests

From the same directory as every other command — the one containing the checkout:

```sh
python3 -m unittest discover -s nbg2ynab/tests
```

---

Not affiliated, associated, or in any way officially connected with YNAB or any of its subsidiaries or affiliates. The official YNAB website can be found at <https://www.ynab.com>.
