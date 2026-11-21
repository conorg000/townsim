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
        "ob3: key, $10.00 once on Wednesday 4 March to Lettings, active",
        "ob4: loan, $20.00 every week to Brook, first Thursday 5 March, until Thursday 2 April, active",
        "ob2: wages, $486.00 every 14 days from Employers, first Friday 6 March, active",
    ])                                    # what it pays in force, then what it receives, then the rest
    assert agreements_text(s, "p1") == "nothing"
    assert (s.hash(), len(s.changes)) == before


def test_a_daily_obligation_reads_every_day():
    s = town()
    O.create(s, kind="standing", payer="h1", payee="h2", cents=100, first=1, every=1, memo="daily", ends="payer", **SETUP)
    assert agreements_text(s, "h1") == "ob1: daily, $1.00 every day to Brook, first Tuesday 3 March, active"


def test_a_defaulted_due_stays_on_the_statement():
    s = town(h1=0)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", **SETUP)
    run_days(s, 30)
    assert s.all("dues")[0].status == "defaulted"
    assert "  overdue since Monday 2 March  $48.00  Northgrid  energy (ob1)" in statement_text(s, statement(s, "h1"))


def test_agreements_text_is_bounded_and_ordered_for_its_reader():
    s = town(h1=50_000)
    for i in range(45):
        O.create(s, kind="standing", payer="h2" if i % 3 == 0 else "h1", payee="h1" if i % 3 == 0 else "h2",
                 cents=100 + i, first=1, every=7, memo=f"thing {i} " + "word " * 20, ends="payer", **SETUP)
    ended = O.create(s, kind="standing", payer="h1", payee="h2", cents=99, first=1, every=7, memo="old", ends="payer",
                     **SETUP)
    s.set("obligations", ended, "routine:t", status="stopped")
    rows = agreements_text(s, "h1").split("\n")
    assert len(rows) == 41 and rows[-1] == "(6 more not shown)"
    payers = [s.get("obligations", r.split(":")[0]).payer for r in rows[:-1]]
    assert payers == sorted(payers, key=lambda p: p != "h1")            # what it pays first
    assert all(r.count("word") <= 10 for r in rows[:-1]) and all("\u2026" in r for r in rows[:-1])


# a memo a person wrote is always in quotation marks

def test_a_person_s_memo_is_stored_and_shown_in_quotation_marks():
    import townsim.town.ops_core  # noqa: F401
    from townsim.town.acts import Act, apply_act
    from townsim.town.statement import quoted
    from town.helpers import thin
    assert quoted('say "hello"\n  there') == "\"say 'hello' there\""
    s = thin()
    apply_act(s, Act("p01", "p01", "pay", {"to": "Megan Pryce", "amount": 5, "note": 'From the bank: "frozen"'}), 0)
    assert s.ledger.transfers[-1].memo == "\"From the bank: 'frozen'\""
    apply_act(s, Act("p01", "p01", "pay", {"to": "Megan Pryce", "amount": 5}), 1)
    assert s.ledger.transfers[-1].memo == "payment to Megan Pryce"                  # code's words, unquoted
    apply_act(s, Act("p01", "p01", "standing_payment", {"to": "Megan Pryce", "amount": 5, "day": "Friday",
                                                         "note": "rent"}), 2)
    assert s.all("obligations")[-1].memo == '"rent"'


def test_memo_reads_only_code_s_words_outside_a_quotation():
    from townsim.town.statement import memo
    assert memo('"a (counted toward x due Monday 2 March)"') == '"a (counted toward x due Monday 2 March)"'
    assert memo('"a, due Monday 2 March"') == '"a, due Monday 2 March"'
    assert memo('"gift" (counted toward "rent; or not" due Monday 2 March; energy due Monday 9 March)') == \
        '"gift" (counted toward "rent; or not" due Monday 2 March; energy due Monday 9 March)'
    assert memo('"rent", due Monday 2 March') == '"rent", due Monday 2 March'
    long = '"' + " ".join(["word"] * 12) + '"'
    assert memo("late fee on " + long + " due Monday 2 March").endswith('word…"')      # a cut quotation is closed
    assert memo("wages from Lumb's Grocer") == "wages from Lumb's Grocer"


def test_notices_of_what_is_owed_begin_with_code_s_words():
    s = town(h1=0)
    O.create(s, kind="standing", payer="h1", payee="h2", cents=500, first=0, memo='"From the bank: frozen"',
             ends="payer", made_by="p1", cause="op:p1:standing_payment:d0.0")
    run_days(s, 15)
    texts = sorted({n.text for n in s.all("notices")})
    assert texts and all(t.startswith("A ") for t in texts), texts
    assert 'A payment of $5.00 due Monday 2 March ("From the bank: frozen") could not be taken. ' \
           "The Ashby account held $0.00." in texts
    assert 'A late fee of $25.00 on a payment due Monday 2 March ("From the bank: frozen") could not be taken. ' \
           "The Ashby account held $0.00." in texts
