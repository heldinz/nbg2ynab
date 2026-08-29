"""Tests for the bits where a silent bug costs real money or real cleanup:
amount parsing, date selection, and import_id stability.
"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nbg2ynab.model import IMPORT_ID_MAX, Transaction, parse_greek_decimal
from nbg2ynab.sources import nbg_csv

HEADER = (
    "Α/Α Συναλλαγής,Ημερομηνία,Ώρα,Valeur,Κατάστημα,Κατηγορία συναλλαγής,"
    "Είδος εργασίας,Ποσό συναλλαγής,Ποσό εντολής,Νόμισμα,Χρέωση / Πίστωση,"
    "Ισοτιμία,Περιγραφή,Λογιστικό Υπόλοιπο,Ονοματεπώνυμο συμβαλλόμενου,"
    "Ονοματεπώνυμο αντισυμβαλλόμενου,Λογαριασμός αντισυμβαλλόμενου,"
    "Τράπεζα αντισυμβαλλόμενου,Επιπρόσθετες πληροφορίες,Αριθμός αναφοράς,"
    "Κανάλι,Ονοματεπώνυμο αντιπροσώπου,Είδος προμήθειας,"
    "Κωδικός εμπόρου/οργανισμού,Σκοπός συναλλαγής,"
    "Ημερομηνία Συναλλαγής με χρεωστική κάρτα,"
    "Ώρα Συναλλαγής με χρεωστική κάρτα,Χρεωστική Κάρτα"
)


def write_csv(tmp: Path, *rows: str) -> Path:
    path = tmp / "statement.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


class TestAmountParsing(unittest.TestCase):
    def test_comma_decimal(self):
        self.assertEqual(parse_greek_decimal("24,50"), Decimal("24.50"))

    def test_dot_thousands_separator(self):
        self.assertEqual(parse_greek_decimal("1.234,56"), Decimal("1234.56"))

    def test_negative(self):
        self.assertEqual(parse_greek_decimal("-1.234,56"), Decimal("-1234.56"))

    def test_parenthesised_negative(self):
        self.assertEqual(parse_greek_decimal("(45,20)"), Decimal("-45.20"))

    def test_plain_integer(self):
        self.assertEqual(parse_greek_decimal("1500"), Decimal("1500"))

    def test_blank_rejected(self):
        with self.assertRaises(ValueError):
            parse_greek_decimal("   ")


class TestMilliunits(unittest.TestCase):
    def _txn(self, amount: str) -> Transaction:
        return Transaction("x", date(2026, 6, 1), Decimal(amount))

    def test_outflow(self):
        self.assertEqual(self._txn("-24.50").milliunits, -24500)

    def test_inflow(self):
        self.assertEqual(self._txn("1500.00").milliunits, 1500000)

    def test_rounds_half_up_not_to_even(self):
        # Decimal's default is ROUND_HALF_EVEN, which would give 4 here.
        self.assertEqual(self._txn("0.0025").milliunits, 3)


class TestImportId(unittest.TestCase):
    def _txn(self, source_id: str, amount="-1.00", day=1) -> Transaction:
        return Transaction(source_id, date(2026, 6, day), Decimal(amount))

    def test_within_ynab_length_limit(self):
        long_id = "Αριθμός αναφοράς=" + "9" * 200
        self.assertLessEqual(len(self._txn(long_id).import_id), IMPORT_ID_MAX)

    def test_stable_across_runs(self):
        self.assertEqual(self._txn("REF1").import_id, self._txn("REF1").import_id)

    def test_independent_of_mutable_fields(self):
        # The bank can restate a payee or shuffle a date. If either fed the
        # import_id, a re-run would create a duplicate instead of a no-op.
        a = Transaction("REF1", date(2026, 6, 1), Decimal("-5"), payee="SHOP")
        b = Transaction("REF1", date(2026, 6, 9), Decimal("-5"), payee="SHOP LTD")
        self.assertEqual(a.import_id, b.import_id)

    def test_distinct_sources_do_not_collide(self):
        self.assertNotEqual(self._txn("REF1").import_id, self._txn("REF2").import_id)


class TestCsvSource(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_prefers_posting_date_over_valeur(self):
        row = (
            '1,25/06/2026,14:22,6/23/26,101,Κάρτες,Αγορά,"-24,50","-24,50",'
            "EUR,Χ,,ΑΓΟΡΑ,\"1.234,56\",ACCOUNT HOLDER,SKLAVENITIS,,,,REF1,POS,,,M1,,"
            "25/06/2026,14:22,****1234"
        )
        txn = nbg_csv.read(write_csv(self.tmp, row))[0]
        self.assertEqual(txn.date, date(2026, 6, 25))

    def test_falls_back_to_valeur_when_posting_date_blank(self):
        row = (
            '1,,14:22,6/23/26,101,Κάρτες,Αγορά,"-24,50","-24,50",'
            "EUR,Χ,,ΑΓΟΡΑ,\"1.234,56\",ACCOUNT HOLDER,SKLAVENITIS,,,,REF1,POS,,,M1,,,,"
        )
        txn = nbg_csv.read(write_csv(self.tmp, row))[0]
        self.assertEqual(txn.date, date(2026, 6, 23))

    def test_unsigned_amount_uses_debit_credit_column(self):
        row = (
            '1,27/06/2026,11:40,6/26/26,101,Κάρτες,Αγορά,"7,80","7,80",'
            "EUR,Χ,,COFFEE ISLAND,\"2.726,76\",ACCOUNT HOLDER,,,,,REF3,POS,,,M9,,,,"
        )
        txn = nbg_csv.read(write_csv(self.tmp, row))[0]
        self.assertEqual(txn.amount, Decimal("-7.80"))

    def test_credit_stays_positive(self):
        row = (
            '2,26/06/2026,09:01,6/24/26,101,Μεταφορές,Μεταφορά,"1.500,00",'
            '"1.500,00",EUR,Π,,ΜΙΣΘΟΔΟΣΙΑ,"2.734,56",ACCOUNT HOLDER,ACME,GR11,NBG,,'
            "REF2,EBANKING,,,,,,,"
        )
        txn = nbg_csv.read(write_csv(self.tmp, row))[0]
        self.assertEqual(txn.amount, Decimal("1500.00"))

    def test_counterparty_wins_over_description_as_payee(self):
        row = (
            '1,25/06/2026,14:22,6/23/26,101,Κάρτες,Αγορά,"-24,50","-24,50",'
            "EUR,Χ,,ΑΓΟΡΑ ΜΕ ΚΑΡΤΑ,\"1.234,56\",ACCOUNT HOLDER,SKLAVENITIS,,,,REF1,"
            "POS,,,M1,,25/06/2026,14:22,****1234"
        )
        txn = nbg_csv.read(write_csv(self.tmp, row))[0]
        self.assertEqual(txn.payee, "SKLAVENITIS")
        self.assertIn("ΑΓΟΡΑ ΜΕ ΚΑΡΤΑ", txn.memo)

    def test_missing_columns_rejected_with_context(self):
        path = self.tmp / "bad.csv"
        path.write_text("foo,bar\n1,2\n", encoding="utf-8")
        with self.assertRaises(nbg_csv.ParseError):
            nbg_csv.read(path)

    def test_blank_trailing_rows_ignored(self):
        row = (
            '1,25/06/2026,14:22,6/23/26,101,Κάρτες,Αγορά,"-24,50","-24,50",'
            "EUR,Χ,,ΑΓΟΡΑ,\"1.234,56\",ACCOUNT HOLDER,SHOP,,,,REF1,POS,,,M1,,,,"
        )
        path = write_csv(self.tmp, row, "," * 27)
        self.assertEqual(len(nbg_csv.read(path)), 1)


class TestYnabPayload(unittest.TestCase):
    def test_never_auto_approved(self):
        payload = Transaction(
            "REF1", date(2026, 6, 1), Decimal("-5")
        ).to_ynab("acct")
        self.assertFalse(payload["approved"])
        self.assertEqual(payload["cleared"], "cleared")

    def test_payee_truncated_to_ynab_limit(self):
        txn = Transaction("REF1", date(2026, 6, 1), Decimal("-5"), payee="X" * 90)
        self.assertEqual(len(txn.to_ynab("acct")["payee_name"]), 50)


if __name__ == "__main__":
    unittest.main()
