"""CSV import of transactions: parse -> validate -> REVIEW -> commit.

Nothing is saved until the user has reviewed the parsed rows and confirmed. Rules:
- Our own export format round-trips: date,type,amount,currency,account,to_account,category,description,debt,principal,interest.
  Header names are case-insensitive; only date, type, amount and account are required (to_account for transfers;
  debt, principal and interest for debt payments).
- Accounts, categories and debts are matched BY NAME against the user's OWN records. Unknown names are errors; nothing is created
  automatically, and another user's records can never be matched.
- Dates: ISO (2026-03-31) or the user's preferred date format. Amounts: plain decimals (1234.56) or properly grouped (1,234.56);
  ambiguous forms such as "12,5" are rejected rather than guessed. Amounts are never negative: the type carries the direction.
- Every row is validated with the same model rules as the web form, then checked against existing transactions to flag likely
  duplicates (unchecked by default on the review page).
- The commit is ALL-OR-NOTHING inside one database transaction and goes through the same locked save path as the web form, so
  rules that depend on earlier rows (e.g. overpaying a debt) are enforced and a failure imports nothing.
- Limits: 1 MB, 1000 rows, UTF-8.
"""
import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.utils import timezone

from .models import Account, Category, Debt, Transaction
from .services import save_transaction

MAX_BYTES = 1_000_000
MAX_ROWS = 1000
T = Transaction.Type
ALIASES = {"to account": "to_account", "to": "to_account", "destination": "to_account", "destination_account": "to_account",
           "payee": "description", "memo": "description", "notes": "description", "creditor": "debt", "principal_amount": "principal",
           "interest_amount": "interest", "account_name": "account", "category_name": "category"}
KNOWN = {"date", "type", "amount", "currency", "account", "to_account", "category", "description", "debt", "principal", "interest"}
REQUIRED = ("date", "type", "amount", "account")
TYPE_WORDS = {"income": T.INCOME, "expense": T.EXPENSE, "transfer": T.TRANSFER, "debt_payment": T.DEBT_PAYMENT, "debt payment": T.DEBT_PAYMENT, "debt-payment": T.DEBT_PAYMENT}
DATE_FORMATS = {"d/m/Y": "%d/%m/%Y", "m/d/Y": "%m/%d/%Y", "Y-m-d": "%Y-%m-%d", "j M Y": "%d %b %Y"}
GROUPED = re.compile(r"^[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?$")      # ASCII digits only: no look-alike numerals
PLAIN = re.compile(r"^[0-9]+(\.[0-9]+)?$")


class FileProblem(Exception):
    """The whole file is unusable (as opposed to individual bad rows)."""


class ImportAborted(Exception):
    def __init__(self, row, message):
        super().__init__(f"Row {row}: {message}")
        self.row, self.message = row, message


@dataclass
class Row:
    number: int
    errors: list = field(default_factory=list)
    duplicate: bool = False
    values: dict = field(default_factory=dict)      # serialisable parsed values (ids, ISO date, strings)
    labels: dict = field(default_factory=dict)      # human-readable text for the review page

    @property
    def ok(self):
        return not self.errors

    def as_dict(self):
        return {"n": self.number, "errors": self.errors, "dup": self.duplicate, "v": self.values, "l": self.labels}


# ------------------------------------------------------------------ parsing helpers
def parse_amount(text):
    s = (text or "").strip().replace("\u00a0", "").lstrip("£$€").strip()
    if s.startswith("-") or s.startswith("("):
        raise ValueError("amounts must not be negative — use the type column (expense, income, ...)")
    if not s:
        raise ValueError("amount is empty")
    if "," in s:
        if not GROUPED.match(s):
            raise ValueError(f"'{text}' is ambiguous (commas must be thousands separators, e.g. 1,234.56)")
        s = s.replace(",", "")
    if not PLAIN.match(s):
        raise ValueError(f"'{text}' is not a valid amount")
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a valid amount")
    if value != value.quantize(Decimal("0.01")):
        raise ValueError(f"'{text}' has more than 2 decimal places")
    return value.quantize(Decimal("0.01"))


def parse_date(text, preferred):
    s = (text or "").strip()
    if not s:
        raise ValueError("date is empty")
    for fmt in ("%Y-%m-%d", DATE_FORMATS.get(preferred, "%d/%m/%Y")):
        try:
            d = dt.datetime.strptime(s, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"'{s}' is not a date (use 2026-03-31 or your preferred format)")
    today = timezone.localdate()
    if d.year < 1990 or d > today + dt.timedelta(days=366):
        raise ValueError(f"{d.isoformat()} is not a plausible date")
    return d


def clean_text(text):
    s = (text or "").strip()
    if len(s) > 1 and s[0] == "'" and s[1] in "=+-@\t\r":      # undo the apostrophe our own CSV export adds against formula injection
        s = s[1:]
    return s


def _decode(data):
    if len(data) > MAX_BYTES:
        raise FileProblem(f"That file is larger than {MAX_BYTES // 1_000_000} MB.")
    if not data.strip():
        raise FileProblem("That file is empty.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileProblem("The file must be UTF-8 text (in Excel: Save As → CSV UTF-8).")


def _reader(text):
    first = text.splitlines()[0] if text.splitlines() else ""
    delimiter = ";" if first.count(";") > first.count(",") else ("\t" if first.count("\t") > first.count(",") else ",")
    return csv.reader(io.StringIO(text), delimiter=delimiter)


# ----------------------------------------------------------------------- preview
def build_preview(user, data):
    """Parse and validate everything; return a list of Row. Raises FileProblem for unusable files. Writes nothing."""
    text = _decode(data)
    reader = _reader(text)
    try:
        header = next(reader)
    except StopIteration:
        raise FileProblem("That file is empty.")
    names = []
    for h in header:
        k = re.sub(r"\s+", " ", h.strip().lower())
        names.append(ALIASES.get(k, k.replace(" ", "_")))
    missing = [r for r in REQUIRED if r not in names]
    if missing:
        raise FileProblem("Missing required column(s): " + ", ".join(missing) + ". Download the template to see the expected columns.")
    if len(set(names)) != len(names):
        raise FileProblem("The header row has duplicate column names.")
    records = list(reader)
    # Row numbers count records as a spreadsheet does (header = line 1) and keep counting over blank lines,
    # so a message like "Row 7" always points at row 7 of the file the user sees.
    body = [(i, cells) for i, cells in enumerate(records, start=2) if any(c.strip() for c in cells)]
    if not body:
        raise FileProblem("The file has a header but no rows.")
    if len(body) > MAX_ROWS:
        raise FileProblem(f"At most {MAX_ROWS} rows can be imported at once (this file has {len(body)}). Split it and import in parts.")

    accounts = {a.name.lower(): a for a in Account.objects.filter(owner=user)}
    cats = {(c.kind, c.name.lower()): c for c in Category.objects.filter(owner=user)}
    debts = {d.creditor.lower(): d for d in Debt.objects.filter(owner=user)}
    preferred = user.profile.date_format
    rows = []
    for i, cells in body:
        row = Row(number=i)
        raw = {n: (cells[j].strip() if j < len(cells) else "") for j, n in enumerate(names) if n in KNOWN}
        _validate(user, row, raw, accounts, cats, debts, preferred)
        rows.append(row)
    _flag_duplicates(user, rows)
    return rows


def _validate(user, row, raw, accounts, cats, debts, preferred):
    err = row.errors
    try:
        day = parse_date(raw.get("date"), preferred)
    except ValueError as e:
        err.append(str(e)); day = None
    kind = TYPE_WORDS.get(re.sub(r"\s+", " ", raw.get("type", "").lower()))
    if kind is None:
        err.append(f"type '{raw.get('type', '')}' is not one of income, expense, transfer, debt_payment")
    try:
        amount = parse_amount(raw.get("amount"))
        if amount <= 0:
            err.append("amount must be greater than zero")
    except ValueError as e:
        err.append(str(e)); amount = None
    acc = accounts.get(raw.get("account", "").lower())
    if not raw.get("account"):
        err.append("account is empty")
    elif acc is None:
        err.append(f"no account named '{raw['account']}' (accounts are never created automatically)")
    elif not acc.is_active:
        err.append(f"account '{acc.name}' is inactive")
    if acc and raw.get("currency") and raw["currency"].upper() != acc.currency:
        err.append(f"currency {raw['currency'].upper()} does not match account '{acc.name}' ({acc.currency})")

    dest = category = debt = None
    principal = interest = Decimal("0.00")
    if kind in (T.INCOME, T.EXPENSE):
        if raw.get("to_account"):
            err.append("to_account only applies to transfers")
        if raw.get("debt"):
            err.append("debt only applies to debt payments")
        for f in ("principal", "interest"):
            if raw.get(f):
                try:
                    if parse_amount(raw[f]) != 0:
                        err.append(f"{f} only applies to debt payments")
                except ValueError as e:
                    err.append(f"{f}: {e}")
        if raw.get("category"):
            want = Category.Kind.INCOME if kind == T.INCOME else Category.Kind.EXPENSE
            category = cats.get((want, raw["category"].lower()))
            if category is None:
                err.append(f"no {want} category named '{raw['category']}'")
    elif kind == T.TRANSFER:
        dest = accounts.get(raw.get("to_account", "").lower())
        if not raw.get("to_account"):
            err.append("to_account is required for a transfer")
        elif dest is None:
            err.append(f"no account named '{raw['to_account']}'")
        elif not dest.is_active:
            err.append(f"account '{dest.name}' is inactive")
        if acc and dest and acc.pk == dest.pk:
            err.append("a transfer needs two different accounts")
        if raw.get("debt"):
            err.append("debt only applies to debt payments")
    elif kind == T.DEBT_PAYMENT:
        debt = debts.get(raw.get("debt", "").lower())
        if not raw.get("debt"):
            err.append("debt is required for a debt payment")
        elif debt is None:
            err.append(f"no debt named '{raw['debt']}'")
        elif debt.status == Debt.Status.CLOSED:
            err.append(f"debt '{debt.creditor}' is closed")
        for f in ("principal", "interest"):
            if not raw.get(f):
                err.append(f"{f} is required for a debt payment (use 0 if none)")
            else:
                try:
                    val = parse_amount(raw[f])
                    if f == "principal":
                        principal = val
                    else:
                        interest = val
                except ValueError as e:
                    err.append(f"{f}: {e}")
        if amount is not None and not any(e.startswith(("principal", "interest")) for e in err) and principal + interest != amount:
            err.append(f"principal ({principal}) + interest ({interest}) must equal the amount ({amount})")
        if debt and acc and debt.currency != acc.currency:
            err.append(f"debt '{debt.creditor}' is in {debt.currency} but account '{acc.name}' is in {acc.currency}")
    description = clean_text(raw.get("description"))
    if len(description) > 200:
        err.append("description is longer than 200 characters")

    if not err:
        tx = Transaction(owner=user, type=kind, date=day, amount=amount, account=acc, destination_account=dest, category=category, description=description,
                         debt=debt, principal_amount=principal, interest_amount=interest)
        try:
            tx.clean()                                              # the same stateless rules the web form enforces
        except ValidationError as e:
            err.extend(e.messages)
    if not err:
        row.values = {"type": kind, "date": day.isoformat(), "amount": str(amount), "account": acc.pk, "dest": dest.pk if dest else None,
                      "category": category.pk if category else None, "description": description, "debt": debt.pk if debt else None,
                      "principal": str(principal), "interest": str(interest)}
    row.labels = {"date": raw.get("date", ""), "type": raw.get("type", ""), "amount": raw.get("amount", ""), "account": raw.get("account", ""),
                  "to": raw.get("to_account") or raw.get("debt") or raw.get("category", ""), "description": description}


def _flag_duplicates(user, rows):
    good = [r for r in rows if r.ok]
    if not good:
        return
    days = [dt.date.fromisoformat(r.values["date"]) for r in good]
    existing = set(Transaction.objects.filter(owner=user, date__gte=min(days), date__lte=max(days))
                   .values_list("date", "type", "amount", "account_id", "destination_account_id", "description", "debt_id"))
    for r in good:
        v = r.values
        key = (dt.date.fromisoformat(v["date"]), v["type"], Decimal(v["amount"]), v["account"], v["dest"], v["description"], v["debt"])
        r.duplicate = key in existing


# ------------------------------------------------------------------------ commit
def commit(user, stored_rows, selected):
    """Create the selected rows, all or nothing. `stored_rows` are the dicts produced by Row.as_dict() at preview time."""
    chosen = [r for r in stored_rows if r["n"] in selected and not r["errors"]]
    accounts = {a.pk: a for a in Account.objects.filter(owner=user)}
    debts = {d.pk: d for d in Debt.objects.filter(owner=user)}
    cats = {c.pk: c for c in Category.objects.filter(owner=user)}
    created = 0
    try:
        with db_transaction.atomic():
            for r in sorted(chosen, key=lambda x: (x["v"]["date"], x["n"])):      # chronological, so balances/limits evolve as they would in life
                v = r["v"]
                try:
                    tx = Transaction(owner=user, type=v["type"], date=dt.date.fromisoformat(v["date"]), amount=Decimal(v["amount"]), account=accounts[v["account"]],
                                     destination_account=accounts.get(v["dest"]), category=cats.get(v["category"]), description=v["description"],
                                     debt=debts.get(v["debt"]), principal_amount=Decimal(v["principal"]), interest_amount=Decimal(v["interest"]))
                except KeyError:
                    raise ImportAborted(r["n"], "an account, debt or category used by this row no longer exists")
                try:
                    save_transaction(tx)
                except ValidationError as e:
                    raise ImportAborted(r["n"], " ".join(e.messages))
                created += 1
    except ImportAborted:
        raise
    return created
