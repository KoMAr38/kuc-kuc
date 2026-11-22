from kuckuc.emergency import emergency_number

# Only the country code matters. The rest of each number is zeros or from a range kept for fiction
# (US 555-01xx, UK 020 7946 0xxx, Australia 0491 570 xxx), so no real line is written down here.


def test_the_emergency_number_follows_the_country_of_the_phone_not_the_language():
    assert emergency_number("+381000000") == "194"  # Serbia: the ambulance number people know
    assert emergency_number("+49000000") == "112"
    assert emergency_number("+43000000") == "112"
    assert emergency_number("+41000000") == "112"
    assert emergency_number("+47000000") == "113"  # Norway: 113 is the ambulance, 112 the police
    assert emergency_number("+12025550123") == "911"
    assert emergency_number("+442079460000") == "999"
    assert emergency_number("+61491570006") == "000"


def test_a_country_or_phone_we_do_not_know_gets_no_number():
    assert emergency_number("+81000000") is None
    assert emergency_number("simulated") is None
    assert emergency_number("") is None
