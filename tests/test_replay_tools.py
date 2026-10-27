"""tools/fetch_history.py and tools/build_timelines.py, against trimmed real responses."""
import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "replay"
sys.path.insert(0, str(ROOT))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fh = _load("fetch_history")
bt = _load("build_timelines")


@pytest.mark.parametrize("st,office,district,dv,want", [
    ("TX", "U.S. Senate", "", None, ("senate", "TX SEN")),
    ("GA", "U.S. Senate (Special)", "", None, ("senate", "GA SEN S")),
    ("PA", "U.S. House", "6TH", None, ("house", "PA-06")),
    ("DE", "US HOUSE", "AT-LARGE", "HOUSE", ("house", "DE-AL")),
    ("NC", "US HOUSE", "001", "HOUSE", ("house", "NC-01")),
    ("GA", "Governor", "", None, ("governor", "GA GOV")),
    ("PA", "AUDITOR GENERAL", "NA", "STATE", ("governor", "PA AUD")),
    ("TX", "STATE SENATE", "005", "STATE", ("state", "TX SD-05")),
    ("TX", "State House", "121", None, ("state", "TX HD-121")),
    ("VT", "STATE HOUSE", "CHITTENDEN-1", "STATE", ("state", "VT LOCAL state-house-chittenden-1")),
    ("DE", "WILMINGTON CITY COUNCIL", "007", "LOCAL", ("local", "DE LOCAL new-castle-wilmington-city-council-007")),
    ("PA", "President", "", None, None),
    ("PA", "Registered Voters", "", None, None),
])
def test_classify(st, office, district, dv, want):
    county = "New Castle" if "WILMINGTON" in office else ""
    assert fh.classify(st, office, district, county, dv) == want


def test_aggregate_medsl_real_sample():
    with (FIX / "medsl_de24_sample.csv").open(newline="") as f:
        rows = fh.aggregate_medsl(f, "DE").rows()
    assert set(rows) == {"senate", "house", "governor", "state", "local"}
    sen = {(r["candidate"], r["county"]): r["votes"] for r in rows["senate"]}
    assert sen[("Lisa Blunt Rochester", "Sussex")] == 206 + 151
    assert {r["race_key"] for r in rows["house"]} == {"DE-AL"}
    assert {r["race_key"] for r in rows["state"]} == {"DE HD-39", "DE SD-17"}


def test_medsl_total_mode_not_double_counted():
    agg = fh.Agg()
    k = ("X SEN", "A", "DEM", "C")
    agg.add("senate", k, 100, "TOTAL")
    agg.add("senate", k, 60, "ELECTION DAY")
    agg.add("senate", k, 40, "ABSENTEE")
    assert agg.rows()["senate"][0]["votes"] == 100


def test_aggregate_openelections_sums_mode_columns():
    with (FIX / "openelections_ga_2020_sample.csv").open(newline="") as f:
        rows = fh.aggregate_openelections(f, "GA").rows()
    assert set(rows) == {"senate", "house", "state"}            # President skipped
    (sp,) = rows["senate"]
    assert sp["race_key"] == "GA SEN S" and sp["party"] == "REP" and sp["votes"] == 5 + 10 + 1 + 5 + 4 + 3
    assert rows["house"][0]["race_key"] == "GA-09"


def test_pick_openelections_files():
    names = ["20201103__pa__general__county.csv", "20201103__pa__general__precinct.csv",
             "20200602__pa__primary__county.csv", "20201103__pa__special__general__state_house__1.csv",
             "20240521__ga__general__primary__county-level.csv", "20240618__ga__general__primary__runoff__county-level.csv"]
    assert fh.pick_openelections_files(names, "2020") == [["20201103__pa__general__county.csv",
                                                          "20201103__pa__general__precinct.csv"]]
    assert fh.pick_openelections_files(names, "2024-primary") == [["20240521__ga__general__primary__county-level.csv"]]


def test_nyt_2024_timeline():
    doc = json.loads((FIX / "nyt_2024_pa_us_senate_trimmed.json").read_text())
    lines = fh.nyt_timeline_2024(doc, "PA")["PA SEN"]
    assert [x["total_votes"] for x in lines] == [5871429, 6963137]     # 0-vote point dropped
    last = {c["name"]: c for c in lines[-1]["candidates"]}
    assert last["Dave McCormick"]["votes"] == 3399295 and last["Dave McCormick"]["party"] == "REP"
    assert lines[0]["synthetic"] is False


def test_nyt_2020_timeline():
    doc = json.loads((FIX / "nyt_2020_ga_senate_trimmed.json").read_text())
    lines = fh.nyt_timeline_2020(doc, "GA")["GA SEN"]
    assert [x["ts"] for x in lines] == sorted(x["ts"] for x in lines)
    first = {c["party"]: c["votes"] for c in lines[0]["candidates"]}
    assert first["REP"] == round(0.648 * 418)


def test_build_timelines_synthetic(tmp_path):
    (tmp_path / "2024").mkdir()
    import shutil
    shutil.copytree(FIX / "2024" / "DE", tmp_path / "2024" / "DE")
    assert bt.run(tmp_path, ["2024"], None, ["senate"]) == 0
    path = tmp_path / "timelines" / "2024" / "DE_SEN.jsonl"
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert len(lines) >= 3 and all(x["synthetic"] for x in lines)
    assert [x["ts"] for x in lines] == sorted(x["ts"] for x in lines)
    tots = [x["total_votes"] for x in lines]
    assert tots == sorted(tots) and tots[-1] == 837 and lines[-1]["expected_vote_pct"] == 100.0
    final = {c["name"]: c["votes"] for c in lines[-1]["candidates"]}
    with (FIX / "2024" / "DE" / "senate.csv").open() as f:
        want = {}
        for r in csv.DictReader(f):
            want[r["candidate"]] = want.get(r["candidate"], 0) + int(r["votes"])
    assert final == want
    # deterministic, and real series are never overwritten
    again = path.read_text()
    bt.run(tmp_path, ["2024"], None, ["senate"], force=True)
    assert path.read_text() == again
    path.write_text(json.dumps({"synthetic": False}) + "\n")
    bt.run(tmp_path, ["2024"], None, ["senate"], force=True)
    assert json.loads(path.read_text())["synthetic"] is False


def test_count_order_from_faq(tmp_path):
    y = tmp_path / "states.yaml"
    y.write_text("AZ:\n  count_order: Mail ballots first, then election-day votes, early in-person last\n")
    assert bt.count_order("AZ", y) == ["mail", "eday", "early"]
    assert bt.count_order("ZZ", y) == bt.DEFAULT_ORDER
