

def test_race_key_2024_dash_names():
    from electionwatch.sources.results.ga_sos import race_key
    assert str(race_key("US House of Representatives - District 6")) == "GA-06"
    assert str(race_key("State Senate - District 5")) == "GA SD-05"
    assert str(race_key("President of the US")) == "GA LOCAL president-of-the-united-states"
