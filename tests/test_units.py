from src.models import round_rupees, units_from_1g


def test_pavun_comes_from_the_rounded_gram_rate():
    assert units_from_1g(11510) == {"g1": 11510, "pavun": 92080, "g10": 115100, "g100": 1151000}


def test_half_up_rounding():
    assert round_rupees(11979.6) == 11980
    assert round_rupees(11510.5) == 11511
    assert units_from_1g(11979.6)["pavun"] == 11980 * 8
