from townsim.town import clock


def test_day_zero_is_monday_2_march():
    assert clock.dow(0) == 0 and clock.date_text(0) == "Monday 2 March"
    assert clock.date_text(14) == "Monday 16 March" and clock.date_text(30) == "Wednesday 1 April"
    assert clock.week(0) == 1 and clock.week(6) == 1 and clock.week(7) == 2


def test_parse_date_reads_what_people_write():
    assert clock.parse_date("Thursday 12 March", today=3) == 10
    assert clock.parse_date("12 March", today=3) == 10
    assert clock.parse_date("thursday", today=3) == 10      # the next Thursday after today
    assert clock.parse_date("Thursday", today=10) == 17     # never today
    assert clock.parse_date("Thursday 5 March", today=10) is None   # in the past
    assert clock.parse_date("soon", today=3) is None and clock.parse_date(None, today=3) is None


def test_parse_date_tolerates_case_spacing_and_a_full_stop():
    assert clock.parse_date("  THURSDAY   12 march. ", today=3) == 10
    assert clock.parse_date("Friday.", today=3) == 4


def test_parse_date_trusts_day_and_month_over_a_wrong_weekday():
    assert clock.parse_date("Monday 12 March", today=3) == 10


def test_parse_date_accepts_today_as_a_date_but_not_as_a_weekday():
    assert clock.parse_date("12 March", today=10) == 10
    assert clock.parse_date("Thursday", today=10) == 17


def test_parse_date_never_raises():
    for junk in (None, 12, 3.5, [], {}, b"12 March", "", "   ", "31 February", "0 March",
                 "12 Marchember", "Thursday 12", "12 13 March", "Thursday Friday",
                 "\u00b2 March", "9" * 5000 + " March", "012 March"):
        assert clock.parse_date(junk, today=3) is None


def test_days_names_the_week_from_monday():
    assert clock.DAYS[0] == "Monday" and clock.DAYS[6] == "Sunday" and len(clock.DAYS) == 7
