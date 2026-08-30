# nbg2ynab

Pushes National Bank of Greece (NBG) statement exports into YNAB over the API.

No dependencies beyond the Python 3.10+ standard library. Nothing leaves your machine except calls to `api.ynab.com`.

## Why this tool exists

I've been using YNAB to manage my personal finances for years. Being in Europe, I was used to entering transactions manually, so this part didn't bother me; my old bank used a consistent sort order for transactions (whether cleared or not) so it was straightforward to keep track of which ones I had entered and which ones were new.

That system fell apart once I moved to Greece. NBG initially sorts transactions by creation date, but then moves them to clearance date, and provides no option to change the sort order. Keeping track of transactions in YNAB became a nightmare, as I had to go back and forth trying to decide if a transaction was genuinely new or if it was one I had previously entered as uncleared. For the first time, I started falling behind on YNAB for weeks at a time.

The old ways weren't working: I needed a new system. If you're an NBG customer who uses YNAB, hopefully it will help you too.

## How it works

This system is aimed at keeping up with transactions periodically rather than continually. To avoid tension with NBG's hard-coded sort order, it opts out of the fight altogether by dealing only with cleared transactions.

The flow:

1. Download an Excel file of transactions
2. Convert it to a CSV
3. Use this tool to send the transactions to YNAB, using transaction matching to avoid duplicates
4. Check NBG's transaction list and approve each equivalent YNAB transaction one by one
5. Match NBG's ledger balance to YNAB's cleared balance and reconcile

## Why the API rather than file import

Every transaction is sent with a deterministic `import_id` derived from the bank's own reference number. YNAB refuses to create a second transaction with an `import_id` it has already seen on that account, which helps us in two ways:

1. **Re-running is not a problem.** Overlapping date ranges don't result in duplicate YNAB transactions, so you don't have to worry about where you got to last time.
2. **Manual entries get merged, not duplicated.** When an `import_id` is present, YNAB also tries to match the import against an existing user-entered transaction on the same account with the same amount and a date within ±10 days, so catching up on a backlog doesn't result in a mess.
3. **Payee renaming only happens when you want it to.** YNAB's own payee rename rules only run on transactions that carry an `import_id`, so imports get your renames and hand-entered transactions don't. (Renaming happens locally too — see [Set custom payee names](#set-custom-payee-names).)

## One-time setup

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

### Fill in `.env`

Three variables to set in order (the third needs the first two to already be set):

1. `YNAB_TOKEN`: YNAB → Account Settings → Developer Settings → New Token.
2. `YNAB_PLAN_ID`: in the URL when you open the plan in the web app.
3. `YNAB_ACCOUNT_ID`: with the first two filled in, run `python3 -m nbg2ynab accounts`, locate your NBG account, and copy its ID.

The format is one `KEY=value` per line, `#` for comments.

### Fill in `payees.json`

The template is an empty `{}`, which is a valid starting point — every payee is title-cased and nothing more until you add rules. See [Set custom payee names](#set-custom-payee-names) for what to put in it; the easiest way to build it up is to run a `--dry-run` and add rules for whatever looks ugly.

## Catch up

Run the following process to catch up on your YNAB transactions for the first time.

### Get a CSV

NBG gives you your transaction list as an XLSX file. Download one covering the period you want to catch up on.

This tool reads CSV rather than XLSX, so the file needs converting. The download has two sheets, `data` then `info`, and only `data` matters here. Delete `info` before you convert: once you have only the `data` sheet left, do "Save As" or "Export" in any spreadsheet app and choose the CSV format to convert it to a single CSV file.

LibreOffice can do the same from the terminal, and needs no `info` tab deletion because it only ever converts the first sheet:

```sh
soffice --headless --convert-to csv statement.xlsx
```

The export's encoding varies, so it's detected automatically (`utf-8-sig`, then `cp1253`, then `iso-8859-7`). If the Greek payees and descriptions come out as garbled Latin characters, the detection picked wrong: force the right one with `--encoding`.

### Set custom payee names

Payees are cleaned up locally, before anything is sent, using the `payees.json` you copied during setup. You can continue to update this file over time.

It works by mapping regex patterns to replacements:

```json
{
  "APPLE\\.COM": "Apple",
  "VODAFONE": "Vodafone Greece",
  "\\.GR\\b": ""
}
```

Rules are tried in order, case-insensitively, and the first match wins:

- **Non-empty replacement**: replaces the whole payee.
- **Empty replacement**: strips only the matched text from the payee and title-cases what's left. Useful for shaving off `.COM`/`.GR` suffixes.
- **No rule matches**: the payee is title-cased as-is.

The file is optional. The default empty `{}` means every payee is title-cased and nothing else.

Both `--dry-run` and `--preview` show the renamed payee, so you can iterate on the rules without touching your live budget.

### Sync to YNAB

The export only contains cleared transactions, so go back further than you think you need (remember that overlap has no downsides).

```sh
# 1. Check the results before touching your budget. Needs no credentials.
python3 -m nbg2ynab push statement.csv --dry-run

# 2. Check what it will actually do to the account: what's already been
#    pushed, what YNAB will merge into a manual entry, what's actually new.
#    Read-only.
python3 -m nbg2ynab push statement.csv --preview

# 3. Push. Everything lands in YNAB as an unapproved transaction.
python3 -m nbg2ynab push statement.csv

# 4. Compare NBG transactions with the unapproved YNAB transactions
#    and approve them one by one. Matched transactions appear merged
#    with your old manual entries; the rest need categorising.

# 5. Find what the match missed.
python3 -m nbg2ynab check --since 2026-06-15
```

Step 2 simulates YNAB's ±10 day amount match locally, against the transactions actually in your account. It is a prediction, not a promise (YNAB does the real matching server-side) but it is where you find out that a catch-up you thought was 200 new transactions is really 190 merges.

Step 5 lists uncleared manual entries with no import attached. Those are the ones where the ±10 day amount match couldn't pair anything — usually because you entered an estimate, or a tip or currency conversion difference changed the amount. Pair each one by hand using **Match** in the YNAB web app, or fix the amount on the manual entry and re-run `push` (which is safe).

Finish by reconciling the account against the bank balance.

## Keep up-to-date

After your initial catch-up, you can focus on keeping YNAB up-to-date on a regular basis, e.g. weekly.

You'll need to download the latest XLSX file and convert it to CSV as before, and then do:

```sh
python3 -m nbg2ynab push statement.csv
```

If you prefer to explicitly limit the sync to the transactions you know are new, find your last YNAB reconciliation date and set it as the `--since` parameter:

```sh
python3 -m nbg2ynab push statement.csv --since <YYYY-MM-DD>
```

Then, approve each transaction and categorize as needed.

## Commands reference

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

## What could go wrong

The whole approach depends on `source_id` coming out the same for a given transaction every time you export it. If it changes, YNAB sees a transaction it doesn't recognise and creates a duplicate instead of skipping it.

For the CSV, `source_id` is the bank's reference number combined with the description and the order amount. The reference on its own isn't enough: NBG gives a fee the same reference number as the transaction that incurred it, so keying on the reference alone would make every fee look like a duplicate of its parent and quietly drop it. (On the rare row with no reference number at all, the dates, amount and description are used instead.)

The trade-off is that the description and the amount now matter too. If NBG restates either one for a transaction you've already imported, or renumbers its references, you'll get a duplicate rather than a no-op. In practice the export has been stable once a transaction clears, but if something looks wrong, this is the first thing to check.

There are two safeguards. `push` warns you when two rows in the same file end up with the same ID, and `check` will show the counts drifting if imports stop landing where you expect. It's worth running `check` after your first few imports, before you start trusting the process.

## Tests

From the same directory as every other command (the one above the `nbg2ynab` clone):

```sh
python3 -m unittest discover -s nbg2ynab/tests
```

## Follow-up work

If downloading and converting a file every week gets old, NBG's PSD2 API could replace the CSV entirely. It would give you two things the export can't: pending transactions, which the XLSX leaves out completely, and no manual download step.

The code is arranged with this in mind. `sources/nbg_csv.py` is the only place that knows how NBG lays out its export, so a `sources/nbg_api.py` returning the same `Transaction` objects would slot in beside it without any changes to `ynab.py`, `model.py`, or the code that builds the YNAB payload.

Two things to get right if you build it:

- **Dates**: use `bookingDateTime` for `Transaction.date` and fall back to `valueDateTime`. That matches the CSV source, which prefers the posting date because it's the order the bank lists transactions in, which in turn is what makes the approval queue easy to work through.
- **IDs**: use `TransactionReference` on its own for `source_id`, and set `cleared` from the booked/pending status. Over the API every entry has its own reference, which isn't true of the CSV (see below), so a transaction moving from pending to booked stays recognisable as the same transaction even if its date, payee, or amount changed along the way. Keep it that way: don't let anything the bank might later revise end up in `source_id`.

---

Not affiliated, associated, or in any way officially connected with YNAB or any of its subsidiaries or affiliates. The official YNAB website can be found at <https://www.ynab.com>.
