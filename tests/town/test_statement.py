from townsim.town import obligations as O
from townsim.town.statement import agreements_text, statement, statement_text

from town.helpers import SETUP, make_rent, make_wage, run_days, town


def test_statement_shows_dates_what_goes_out_and_what_is_left():
    s = town(h1=73_192); make_rent(s, cents=17_500, first=0, every=7); make_wage(s, "h1", cents=48_600, first=4)
    run_days(s, 2)
    hash_before_calling_statement, logged = s.hash(), len(s.changes)
    text = statement_text(s, statement(s, "h1"))
    assert "$556.92" in text and "Monday 2 March" in text and "Monday 9 March" in text and "Friday 6 March" in text
    assert "$381.92" in text          # after the next rent goes out; the wage is not netted in
    assert s.hash() == hash_before_calling_statement   # building a statement changes nothing
    assert len(s.changes) == logged
    assert text == "\n".join([
        "Balance: $556.92",
        "In and out since Monday 2 March:",
        "  Monday 2 March  $731.92  Employers  opening",
        "  Monday 2 March  -$175.00  Lettings  rent, due Monday 2 March",
        "Going out by Wednesday 11 March:",
        "  Monday 9 March  $175.00  Lettings  rent (ob1)",
        "Expected in by Wednesday 11 March:",
        "  Friday 6 March  $486.00  Employers  wages (ob2)",
        "After what goes out: $381.92",
    ])


def test_statement_fields():
    s = town(h1=73_192); make_rent(s, cents=17_500, first=0, every=7); make_wage(s, "h1", cents=48_600, first=4)
    run_days(s, 2)
    st = statement(s, "h1")
    assert (st.account, st.day, st.balance, st.after_out, st.earlier) == ("h1", 2, 55_692, 38_192, 0)
    assert [(l.day, l.other, l.cents) for l in st.lines] == [(0, "outside:employers", 73_192), (0, "outside:lettings", -17_500)]
    assert [u.memo for u in st.out] == ["rent"] and [u.memo for u in st.coming_in] == ["wages"]


def test_old_lines_drop_off_and_long_statements_keep_the_newest():
    s = town(h1=100_000)
    O.create(s, kind="standing", payer="h1", payee="h2", cents=100, first=0, every=1, memo="daily", ends="payer", **SETUP)
    run_days(s, 30)
    st = statement(s, "h1", back=14)
    assert [l.day for l in st.lines] == list(range(16, 30)) and st.earlier == 0
    st = statement(s, "h1", back=14, max_lines=5)
    assert [l.day for l in st.lines] == list(range(25, 30)) and st.earlier == 9
    text = statement_text(s, st)
    assert "In and out since Wednesday 18 March:\n  (9 earlier lines not shown)\n  Friday 27 March  -$1.00  Brook  daily" in text


def test_arrears_show_as_overdue_and_empty_sections_say_nothing():
    s = town(h1=0)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", **SETUP)
    run_days(s, 3)
    st = statement(s, "h1")
    assert st.after_out == -4_800
    assert statement_text(s, st) == "\n".join([
        "Balance: $0.00",
        "In and out since Monday 2 March:",
        "  nothing",
        "Going out by Thursday 12 March:",
        "  overdue since Monday 2 March  $48.00  Northgrid  energy (ob1)",
        "Expected in by Thursday 12 March:",
        "  nothing",
        "After what goes out: -$48.00",
    ])


def test_agreements_text_lists_everything_paid_or_received():
    s = town(h1=50_000)
    rent = make_rent(s, cents=17_500, first=0, every=7)
    make_wage(s, "h1", cents=48_600, first=4, every=14)
    O.create(s, kind="fee", payer="h1", payee="outside:lettings", cents=1_000, first=2, memo="key", ends="payer", **SETUP)
    O.create(s, kind="loan", payer="h1", payee="h2", cents=2_000, first=3, every=7, last=31, memo="loan", ends="both", **SETUP)
    O.create(s, kind="bill", payer="h2", payee="outside:northgrid", cents=4_800, first=1, memo="not ours", ends="service", **SETUP)
    O.stop(s, rent, "h1", "op:p1:stop:d0.0")
    before = s.hash(), len(s.changes)
    assert agreements_text(s, "h1") == "\n".join([
        "ob1: rent, $175.00 every week to Lettings, first Monday 2 March, breached",
        "ob2: wages, $486.00 every 14 days from Employers, first Friday 6 March, active",
        "ob3: key, $10.00 once on Wednesday 4 March to Lettings, active",
        "ob4: loan, $20.00 every week to Brook, first Thursday 5 March, until Thursday 2 April, active",
    ])
    assert agreements_text(s, "p1") == "nothing"
    assert (s.hash(), len(s.changes)) == before
