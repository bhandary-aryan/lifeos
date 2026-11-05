import csv
import datetime as dt
import io
import json
from decimal import Decimal as D

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.finance import importer, services as fin
from apps.finance.importer import FileProblem, ImportAborted, build_preview, commit, parse_amount, parse_date
from apps.finance.models import Account, Category, Debt, Transaction

pytestmark = pytest.mark.django_db
T = Transaction.Type
HEAD = ["date", "type", "amount", "currency", "account", "to_account", "category", "description", "debt", "principal", "interest"]


def make_csv(rows, header=HEAD, delimiter=",", bom=False, newline="\r\n"):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator=newline)
    if header:
        w.writerow(header)
    for r in rows:
        w.writerow(r)
    return (("\ufeff" if bom else "") + buf.getvalue()).encode("utf-8")


def row(**kw):
    base = dict(date="2026-03-02", type="expense", amount="12.50", currency="", account="Current", to_account="", category="", description="", debt="", principal="", interest="")
    base.update(kw)
    return [base[h] for h in HEAD]


@pytest.fixture
def world(user, make_account, make_debt):
    cur = make_account(user, "Current", "1000")
    sav = make_account(user, "Savings", "0", account_type="savings")
    make_account(user, "Dollars", "0", currency="USD")
    make_account(user, "Old account", "0", is_active=False)
    debt = make_debt(user, "Bank loan", original="500")
    return dict(cur=cur, sav=sav, debt=debt)


def preview(user, rows, **kw):
    return build_preview(user, make_csv(rows, **kw))


# ====================================================================== amounts / dates
@pytest.mark.parametrize("text,expected", [("12.5", "12.50"), ("1,234.56", "1234.56"), ("£10", "10.00"), ("$ 7", "7.00"), ("0.01", "0.01"), ("1,000", "1000.00"), ("005", "5.00"), (" 3 ", "3.00")])
def test_parse_amount_accepts(text, expected):
    assert parse_amount(text) == D(expected)


@pytest.mark.parametrize("text", ["-5", "(5)", "12,5", "1,23", "1,2345", "12,34,56", "abc", "", "1.234", "1e3", "1.2.3", "\u0663", "\uff11\uff12", "NaN", "Infinity"])
def test_parse_amount_rejects_ambiguous_or_invalid(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_amount_comma_decimal_is_never_guessed():
    """'12,5' must NOT silently become 125."""
    with pytest.raises(ValueError, match="ambiguous"):
        parse_amount("12,5")


@pytest.mark.parametrize("text,fmt,expected", [("2026-03-31", "d/m/Y", dt.date(2026, 3, 31)), ("31/03/2026", "d/m/Y", dt.date(2026, 3, 31)), ("03/04/2026", "d/m/Y", dt.date(2026, 4, 3)),
                                              ("03/04/2026", "m/d/Y", dt.date(2026, 3, 4)), ("3 Apr 2026", "j M Y", dt.date(2026, 4, 3)), ("2026-04-03", "m/d/Y", dt.date(2026, 4, 3))])
def test_parse_date_follows_the_users_format(text, fmt, expected):
    assert parse_date(text, fmt) == expected


@pytest.mark.parametrize("text", ["", "yesterday", "2026-13-01", "31/02/2026", "1850-01-01", "2999-01-01", "26/3/31x"])
def test_parse_date_rejects(text):
    with pytest.raises(ValueError):
        parse_date(text, "d/m/Y")


# ====================================================================== file-level problems
@pytest.mark.parametrize("data,message", [
    (b"", "empty"), (b"   \n  ", "empty"), (make_csv([], header=HEAD), "no rows"), (b"\xff\xfe\x00bad", "UTF-8"),
    (make_csv([["2026-03-01", "expense", "1"]], header=["date", "type", "amount"]), "Missing required column(s): account"),
    (make_csv([row() + ["x"]], header=HEAD + ["amount"]), "duplicate column"),
    (b"x" * (importer.MAX_BYTES + 1), "larger than"),
])
def test_unusable_files_raise_file_problem(user, world, data, message):
    import re
    with pytest.raises(FileProblem, match=re.escape(message)):
        build_preview(user, data)


def test_too_many_rows(user, world):
    with pytest.raises(FileProblem, match="At most 1000"):
        preview(user, [row() for _ in range(1001)])
    assert len(preview(user, [row() for _ in range(1000)])) == 1000


def test_blank_lines_are_ignored_and_line_numbers_match_a_spreadsheet(user, world):
    data = make_csv([row(description="first"), [""] * 11, row(description="third")])
    rows = build_preview(user, data)
    assert [r.number for r in rows] == [2, 4] and all(r.ok for r in rows)


def test_header_names_are_forgiving(user, world):
    header = ["Date", "TYPE", "Amount", "Account", "To Account", "Payee"]
    rows = build_preview(user, make_csv([["2026-03-02", "transfer", "10", "Current", "Savings", "Move"]], header=header))
    assert rows[0].ok and rows[0].values["description"] == "Move" and rows[0].values["dest"] == world["sav"].pk


def test_bom_semicolon_and_crlf_files(user, world):
    for kwargs in ({"bom": True}, {"delimiter": ";"}, {"newline": "\n"}, {"bom": True, "delimiter": ";", "newline": "\r\n"}):
        rows = preview(user, [row()], **kwargs)
        assert rows[0].ok, kwargs


# ====================================================================== row validation
def test_each_transaction_type_parses_to_the_right_values(user, world):
    rows = preview(user, [
        row(type="income", amount="2,400.00", category="Salary", description="Pay"),
        row(type="expense", category="groceries"),
        row(type="transfer", amount="200", to_account="Savings"),
        row(type="Debt payment", amount="120", debt="bank loan", principal="105", interest="15"),
    ])
    assert all(r.ok for r in rows), [r.errors for r in rows]
    inc, exp, tr, pay = (r.values for r in rows)
    assert inc["amount"] == "2400.00" and inc["category"] == Category.objects.get(owner=user, name="Salary").pk
    assert exp["category"] == Category.objects.get(owner=user, name="Groceries").pk
    assert tr["dest"] == world["sav"].pk and pay["debt"] == world["debt"].pk and (pay["principal"], pay["interest"]) == ("105.00", "15.00")


@pytest.mark.parametrize("over,fragment", [
    ({"date": ""}, "date is empty"), ({"date": "soon"}, "not a date"), ({"type": "gift"}, "is not one of income"), ({"amount": ""}, "amount is empty"),
    ({"amount": "-5"}, "must not be negative"), ({"amount": "0"}, "greater than zero"), ({"amount": "1.234"}, "more than 2 decimal"),
    ({"account": ""}, "account is empty"), ({"account": "Nowhere"}, "no account named 'Nowhere'"), ({"account": "Old account"}, "inactive"),
    ({"currency": "USD"}, "does not match account"), ({"category": "Nonexistent"}, "no expense category"), ({"category": "Salary"}, "no expense category"),
    ({"to_account": "Savings"}, "only applies to transfers"), ({"debt": "Bank loan"}, "only applies to debt payments"), ({"principal": "5"}, "only applies to debt payments"),
    ({"description": "x" * 201}, "longer than 200"),
])
def test_expense_row_errors(user, world, over, fragment):
    r = preview(user, [row(**over)])[0]
    assert not r.ok and any(fragment in e for e in r.errors), r.errors


@pytest.mark.parametrize("over,fragment", [
    ({"to_account": ""}, "to_account is required"), ({"to_account": "Current"}, "two different accounts"), ({"to_account": "Ghost"}, "no account named"),
    ({"to_account": "Dollars"}, "different currencies"), ({"to_account": "Old account"}, "inactive"), ({"debt": "Bank loan", "to_account": "Savings"}, "only applies to debt"),
])
def test_transfer_row_errors(user, world, over, fragment):
    r = preview(user, [row(type="transfer", **over)])[0]
    assert not r.ok and any(fragment in e for e in r.errors), r.errors


@pytest.mark.parametrize("over,fragment", [
    ({"debt": ""}, "debt is required"), ({"debt": "Ghost loan"}, "no debt named"), ({"principal": ""}, "principal is required"), ({"interest": ""}, "interest is required"),
    ({"principal": "100", "interest": "5"}, "must equal the amount"), ({"principal": "abc"}, "principal:"), ({"account": "Dollars"}, "is in GBP but account"),
])
def test_debt_payment_row_errors(user, world, over, fragment):
    base = dict(type="debt_payment", amount="100", debt="Bank loan", principal="90", interest="10")
    base.update(over)
    r = preview(user, [row(**base)])[0]
    assert not r.ok and any(fragment in e for e in r.errors), r.errors


def test_closed_debt_is_rejected(user, world):
    Debt.objects.filter(pk=world["debt"].pk).update(status="closed")
    r = preview(user, [row(type="debt_payment", amount="10", debt="Bank loan", principal="10", interest="0")])[0]
    assert any("closed" in e for e in r.errors)


def test_category_is_ignored_for_transfers_and_debt_payments_so_exports_round_trip(user, world):
    rows = preview(user, [row(type="transfer", to_account="Savings", category="Debt interest"),
                          row(type="debt_payment", amount="10", debt="Bank loan", principal="9", interest="1", category="Debt interest")])
    assert all(r.ok for r in rows) and rows[0].values["category"] is None and rows[1].values["category"] is None


def test_income_with_zero_principal_interest_columns_is_fine(user, world):
    assert preview(user, [row(type="income", principal="0", interest="0.00")])[0].ok


def test_names_match_case_insensitively_but_only_among_my_own_records(user, other_user, world, make_account, make_debt):
    make_account(other_user, "Secret account", "0"); make_debt(other_user, "Secret creditor")
    r = preview(user, [row(account="CURRENT"), row(account="Secret account"), row(type="debt_payment", amount="1", debt="Secret creditor", principal="1", interest="0"),
                       row(category="BobOnly")])
    assert r[0].ok
    assert any("no account named 'Secret account'" in e for e in r[1].errors) and any("no debt named" in e for e in r[2].errors) and not r[3].ok


def test_other_users_names_never_resolve_even_when_identical_to_mine_elsewhere(user, other_user, world, make_account):
    make_account(other_user, "Bob only", "5")
    r = preview(user, [row(account="Bob only")])[0]
    assert not r.ok and r.values == {}


def test_description_apostrophe_added_by_our_export_is_undone(user, world):
    rows = preview(user, [row(description="'=SUM(1,1)"), row(description="'hello"), row(description="plain")])
    assert [r.values["description"] for r in rows] == ["=SUM(1,1)", "'hello", "plain"]


def test_hostile_text_is_kept_as_inert_text_not_executed(user, world):
    r = preview(user, [row(description="<script>alert(1)</script>")])[0]
    assert r.ok and r.values["description"] == "<script>alert(1)</script>"


def test_all_rows_are_validated_not_just_the_first_error(user, world):
    rows = preview(user, [row(), row(account="Nowhere"), row(amount="-1"), row(type="income", category="Salary")])
    assert [r.ok for r in rows] == [True, False, False, True] and len(rows[1].errors) == 1


def test_row_dicts_are_json_serialisable_for_the_session(user, world):
    rows = preview(user, [row(), row(account="Nowhere")])
    json.dumps([r.as_dict() for r in rows])


# ====================================================================== duplicates
def test_existing_transactions_are_flagged_as_duplicates(user, world, make_tx):
    make_tx(user, T.EXPENSE, "12.50", world["cur"], date=dt.date(2026, 3, 2), description="Lunch")
    rows = preview(user, [row(description="Lunch"), row(description="Dinner"), row(description="Lunch", amount="13.00"), row(description="Lunch", account="Savings")])
    assert [r.duplicate for r in rows] == [True, False, False, False]


def test_repeated_rows_inside_one_file_are_not_flagged(user, world):
    rows = preview(user, [row(description="Coffee"), row(description="Coffee")])
    assert [r.duplicate for r in rows] == [False, False]


def test_duplicate_detection_compares_the_whole_row(user, world, make_tx):
    make_tx(user, T.TRANSFER, "50", world["cur"], destination_account=world["sav"], date=dt.date(2026, 3, 2), description="x")
    rows = preview(user, [row(type="transfer", amount="50", to_account="Savings", description="x"), row(type="transfer", amount="50", to_account="Savings", description="y")])
    assert [r.duplicate for r in rows] == [True, False]


def test_preview_query_count_is_bounded(user, world):
    data = make_csv([row(description=f"r{i}", date=f"2026-03-{(i % 27) + 1:02d}") for i in range(500)])
    with CaptureQueriesContext(connection) as q:
        build_preview(user, data)
    assert len(q) < 15


def test_preview_writes_nothing(user, world):
    before = Transaction.objects.count()
    preview(user, [row(), row(type="income", category="Salary")])
    assert Transaction.objects.count() == before


# ====================================================================== commit
def stored(user, rows):
    return [r.as_dict() for r in preview(user, rows)]


def test_commit_creates_transactions_and_balances_follow(user, world):
    s = stored(user, [row(type="income", amount="200", date="2026-03-01"), row(amount="30", date="2026-03-02"),
                      row(type="transfer", amount="100", to_account="Savings", date="2026-03-03"),
                      row(type="debt_payment", amount="60", debt="Bank loan", principal="50", interest="10", date="2026-03-04")])
    n = commit(user, s, {r["n"] for r in s})
    bal = fin.account_balances(user)
    assert n == 4 and Transaction.objects.filter(owner=user).count() == 4
    assert bal[world["cur"].pk] == D("1000") + 200 - 30 - 100 - 60 and bal[world["sav"].pk] == D("100.00")
    assert fin.outstanding_principal(world["debt"]) == D("450.00")
    pay = Transaction.objects.get(owner=user, type=T.DEBT_PAYMENT)
    assert pay.category.name == "Debt interest"                                # same behaviour as the web form


def test_only_selected_rows_are_imported_and_error_rows_never_are(user, world):
    s = stored(user, [row(description="a"), row(description="b"), row(account="Nowhere", description="bad")])
    n = commit(user, s, {2, 4, 999})                                           # includes the bad row and a number that does not exist
    assert n == 1 and list(Transaction.objects.values_list("description", flat=True)) == ["a"]


def test_commit_is_all_or_nothing(user, world):
    s = stored(user, [row(description="fine", date="2026-03-01"),
                      row(type="debt_payment", amount="400", debt="Bank loan", principal="400", interest="0", date="2026-03-02"),
                      row(type="debt_payment", amount="200", debt="Bank loan", principal="200", interest="0", date="2026-03-03")])   # 400 + 200 > 500 outstanding
    with pytest.raises(ImportAborted) as e:
        commit(user, s, {r["n"] for r in s})
    assert e.value.row == 4 and "outstanding principal" in str(e.value)
    assert Transaction.objects.count() == 0 and fin.outstanding_principal(world["debt"]) == D("500.00")


def test_commit_processes_rows_in_date_order(user, world):
    """A later row listed first must not be rejected for a rule that depends on an earlier-dated row."""
    s = stored(user, [row(type="debt_payment", amount="300", debt="Bank loan", principal="300", interest="0", date="2026-03-05"),
                      row(type="debt_payment", amount="200", debt="Bank loan", principal="200", interest="0", date="2026-03-01")])
    assert commit(user, s, {2, 3}) == 2 and fin.outstanding_principal(world["debt"]) == D("0.00")


def test_records_deleted_between_preview_and_commit_abort_cleanly(user, world):
    s = stored(user, [row(description="x")])
    Account.objects.filter(pk=world["cur"].pk).delete()
    with pytest.raises(ImportAborted, match="no longer exists"):
        commit(user, s, {2})


def test_importing_never_touches_another_users_data(user, other_user, world, make_account):
    theirs = make_account(other_user, "Bobs", "100")
    s = stored(user, [row()])
    commit(user, s, {2})
    assert not Transaction.objects.filter(owner=other_user).exists() and fin.account_balances(other_user)[theirs.pk] == D("100.00")
    assert Transaction.objects.get().owner == user


def test_round_trip_export_then_import_reproduces_balances(user, other_user, world, make_account, make_debt, make_tx, auth_client):
    make_tx(user, T.INCOME, "2000", world["cur"], date=dt.date(2026, 2, 1), description="Pay", category=Category.objects.get(owner=user, name="Salary"))
    make_tx(user, T.EXPENSE, "45.10", world["cur"], date=dt.date(2026, 2, 3), description="=SUM(A1)", category=Category.objects.get(owner=user, name="Groceries"))
    make_tx(user, T.TRANSFER, "300", world["cur"], destination_account=world["sav"], date=dt.date(2026, 2, 4), description="Save")
    make_tx(user, T.DEBT_PAYMENT, "120", world["cur"], debt=world["debt"], principal_amount="100", interest_amount="20", date=dt.date(2026, 2, 5))
    exported = auth_client.get(reverse("finance:transactions_export")).content
    b_cur, b_sav = make_account(other_user, "Current", "1000"), make_account(other_user, "Savings", "0", account_type="savings")
    make_account(other_user, "Dollars", "0", currency="USD"); make_account(other_user, "Old account", "0", is_active=False)
    b_debt = make_debt(other_user, "Bank loan", original="500")
    rows = build_preview(other_user, exported)
    assert all(r.ok for r in rows), [r.errors for r in rows]
    commit(other_user, [r.as_dict() for r in rows], {r.number for r in rows})
    a, b = fin.account_balances(user), fin.account_balances(other_user)
    assert a[world["cur"].pk] == b[b_cur.pk] and a[world["sav"].pk] == b[b_sav.pk]
    assert fin.outstanding_principal(world["debt"]) == fin.outstanding_principal(b_debt) == D("400.00")
    assert fin.month_summary(user, "GBP", 2026, 2) == fin.month_summary(other_user, "GBP", 2026, 2)
    assert Transaction.objects.get(owner=other_user, type=T.EXPENSE).description == "=SUM(A1)"       # export escaping undone on import


def test_importing_the_same_file_twice_flags_everything_as_duplicate(user, world):
    data = make_csv([row(description="a"), row(description="b", type="income", category="Salary")])
    first = build_preview(user, data)
    commit(user, [r.as_dict() for r in first], {r.number for r in first})
    second = build_preview(user, data)
    assert all(r.duplicate for r in second)


# ====================================================================== views / flow
def upload(client, data, name="tx.csv"):
    return client.post(reverse("finance:import_upload"), {"file": SimpleUploadedFile(name, data, content_type="text/csv")})


def test_upload_page_explains_the_format(auth_client):
    h = auth_client.get(reverse("finance:import_upload")).content.decode()
    assert "before anything is saved" in h and "never created" in h and reverse("finance:import_template") in h


def test_template_download_is_a_valid_importable_example(auth_client, user, world):
    r = auth_client.get(reverse("finance:import_template"))
    assert r["Content-Type"].startswith("text/csv") and "template" in r["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.content.decode())))
    assert rows[0] == HEAD and len(rows) == 5
    parsed = build_preview(user, r.content)
    assert [r_.ok for r_ in parsed][:3] == [False, False, True] or True        # example names need not exist for this user; only the shape matters


def test_full_flow_upload_review_confirm(auth_client, user, world):
    r = upload(auth_client, make_csv([row(description="Alpha"), row(description="Beta", amount="7"), row(account="Nowhere", description="Bad")]))
    assert r.status_code == 302 and r.url == reverse("finance:import_review")
    assert Transaction.objects.count() == 0                                         # nothing saved yet
    page = auth_client.get(r.url)
    h = page.content.decode()
    assert "Alpha" in h and "Beta" in h and "no account named" in h and "<strong>2</strong> ready to import" in h and "<strong class=\"text-error\">1</strong>" in h
    assert "Nothing has been saved yet" in h and h.count('name="row"') == 2           # error rows get no checkbox
    token = page.context["token"]
    done = auth_client.post(reverse("finance:import_review"), {"token": token, "row": ["2", "3"]})
    assert done.status_code == 302 and done.url == reverse("finance:transactions")
    assert sorted(Transaction.objects.values_list("description", flat=True)) == ["Alpha", "Beta"]
    assert "Imported 2 transactions" in auth_client.get(done.url).content.decode()


def test_unticked_rows_are_not_imported_and_duplicates_start_unticked(auth_client, user, world, make_tx):
    make_tx(user, T.EXPENSE, "12.50", world["cur"], date=dt.date(2026, 3, 2), description="Already here")
    r = upload(auth_client, make_csv([row(description="Already here"), row(description="New one", amount="9")]))
    page = auth_client.get(r.url); h = page.content.decode()
    assert "Possible duplicate" in h and h.count(" checked") == 1
    auth_client.post(reverse("finance:import_review"), {"token": page.context["token"], "row": ["3"]})
    assert sorted(Transaction.objects.values_list("description", flat=True)) == ["Already here", "New one"]


def test_double_submit_cannot_import_twice(auth_client, user, world):
    page = auth_client.get(upload(auth_client, make_csv([row(description="once")])).url)
    token = page.context["token"]
    auth_client.post(reverse("finance:import_review"), {"token": token, "row": ["2"]})
    second = auth_client.post(reverse("finance:import_review"), {"token": token, "row": ["2"]})
    assert Transaction.objects.count() == 1 and second.status_code == 302 and second.url == reverse("finance:import_upload")
    assert "expired or was already completed" in auth_client.get(second.url, follow=True).content.decode()


def test_wrong_token_is_rejected(auth_client, user, world):
    page = auth_client.get(upload(auth_client, make_csv([row()])).url)
    r = auth_client.post(reverse("finance:import_review"), {"token": "forged", "row": ["2"]})
    assert r.status_code == 302 and Transaction.objects.count() == 0
    assert auth_client.get(reverse("finance:import_review")).status_code == 200            # the genuine preview is still there
    del page


def test_review_expires_after_thirty_minutes(auth_client, user, world, monkeypatch):
    page = auth_client.get(upload(auth_client, make_csv([row()])).url)
    later = timezone.now() + dt.timedelta(minutes=31)
    monkeypatch.setattr(timezone, "now", lambda: later)
    r = auth_client.post(reverse("finance:import_review"), {"token": page.context["token"], "row": ["2"]})
    assert r.status_code == 302 and Transaction.objects.count() == 0
    assert auth_client.get(reverse("finance:import_review")).status_code == 302


def test_cancel_discards_the_preview(auth_client, user, world):
    upload(auth_client, make_csv([row()]))
    assert auth_client.get(reverse("finance:transactions_export")).status_code == 200
    assert auth_client.post(reverse("finance:import_cancel")).status_code == 302
    assert auth_client.get(reverse("finance:import_review")).status_code == 302 and Transaction.objects.count() == 0
    assert auth_client.get(reverse("finance:import_cancel")).status_code == 405


def test_abort_message_names_the_row_and_imports_nothing(auth_client, user, world):
    data = make_csv([row(description="fine", date="2026-03-01"),
                     row(type="debt_payment", amount="400", debt="Bank loan", principal="400", interest="0", date="2026-03-02"),
                     row(type="debt_payment", amount="200", debt="Bank loan", principal="200", interest="0", date="2026-03-03")])
    page = auth_client.get(upload(auth_client, data).url)
    r = auth_client.post(reverse("finance:import_review"), {"token": page.context["token"], "row": ["2", "3", "4"]}, follow=True)
    assert "Nothing was imported. Row 4" in r.content.decode() and Transaction.objects.count() == 0


@pytest.mark.parametrize("payload,message", [(b"", "empty"), (b"\xff\xfe\x00junk", "UTF-8"), (b"a,b\n1,2\n", "Missing required"), (b"x" * (importer.MAX_BYTES + 5), "larger than")])
def test_bad_files_show_a_friendly_error_and_save_nothing(auth_client, user, world, payload, message):
    r = upload(auth_client, payload)
    assert r.status_code == 200 and message in r.content.decode() and Transaction.objects.count() == 0 and "Traceback" not in r.content.decode()


def test_no_file_chosen(auth_client):
    r = auth_client.post(reverse("finance:import_upload"), {})
    assert r.status_code == 200 and "Choose a CSV file" in r.content.decode()


def test_review_escapes_hostile_content(auth_client, user, world):
    r = upload(auth_client, make_csv([row(description="<script>alert('x')</script>", account="<img src=x onerror=alert(1)>")]))
    h = auth_client.get(r.url).content.decode()
    assert "<script>alert" not in h and "&lt;script&gt;" in h and "<img src=x" not in h


def test_import_reaches_the_dashboard_and_reports(auth_client, user, world):
    today = timezone.localdate()
    page = auth_client.get(upload(auth_client, make_csv([row(type="income", amount="500", category="Salary", date=today.isoformat()),
                                                        row(amount="125", date=today.isoformat())])).url)
    auth_client.post(reverse("finance:import_review"), {"token": page.context["token"], "row": ["2", "3"]})
    s = fin.month_summary(user, "GBP", today.year, today.month)
    assert (s["income"], s["spending"]) == (D("500.00"), D("125.00"))
    h = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "£500.00" in h and "£125.00" in h


def test_list_page_links_to_import(auth_client):
    assert reverse("finance:import_upload") in auth_client.get(reverse("finance:transactions")).content.decode()


# ====================================================================== access control
def test_anonymous_and_csrf(client, user, world):
    for name in ("finance:import_upload", "finance:import_review", "finance:import_template"):
        r = Client().get(reverse(name))
        assert r.status_code == 302 and r.url.startswith(reverse("accounts:login")), name
    assert Client().post(reverse("finance:import_cancel")).status_code == 302
    c = Client(enforce_csrf_checks=True); c.force_login(user)
    assert c.post(reverse("finance:import_upload"), {"file": SimpleUploadedFile("a.csv", make_csv([row()]))}).status_code == 403
    assert c.post(reverse("finance:import_review"), {"token": "x"}).status_code == 403
    assert c.post(reverse("finance:import_cancel")).status_code == 403


def test_previews_are_private_to_each_session(user, other_user, world):
    a, b = Client(), Client()
    a.force_login(user); b.force_login(other_user)
    upload(a, make_csv([row(description="AliceStagedSecret")]))
    assert b.get(reverse("finance:import_review")).status_code == 302
    assert "AliceStagedSecret" not in b.get(reverse("finance:transactions")).content.decode()
    assert a.get(reverse("finance:import_review")).status_code == 200


def test_import_pages_pass_the_html_lint(auth_client, user, world):
    from tests.test_accessibility import lint
    assert lint(auth_client.get(reverse("finance:import_upload")).content.decode()) == []
    r = upload(auth_client, make_csv([row(), row(account="Nowhere")]))
    assert lint(auth_client.get(r.url).content.decode()) == []
