from electionwatch.sources.results import _common as c


def test_norm_party_maps_minnesota_dfl_to_dem():
    assert c.norm_party("DFL") == "DEM"
    assert c.norm_party("Democratic-Farmer-Labor") == "DEM"


def test_norm_party_maps_gop_and_r_to_rep():
    assert c.norm_party("GOP") == "REP"
    assert c.norm_party("R") == "REP"


def test_norm_party_handles_empty_and_none():
    assert c.norm_party("") is None
    assert c.norm_party(None) is None
