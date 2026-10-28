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


@pytest.mark.parametrize(
    "st,office,district,dv,want",
    [
        ("TX", "U.S. Senate", "", None, ("senate", "TX SEN")),
        ("GA", "U.S. Senate (Special)", "", None, ("senate", "GA SEN S")),
        ("PA", "U.S. House", "6TH", None, ("house", "PA-06")),
        ("DE", "US HOUSE", "AT-LARGE", "HOUSE", ("house", "DE-AL")),
        ("NC", "US HOUSE", "001", "HOUSE", ("house", "NC-01")),
        ("KS", "U.S. House District 7", "", None, ("house", "KS-07")),
        ("CT", "Congress", "CD3", None, ("house", "CT-03")),
        ("CT", "Congress", "", None, None),  # no district anywhere: dropped, not CT-AL
        ("CT", "Attorney", "", None, ("governor", "CT AG")),
        ("CT", "Secretary", "", None, ("governor", "CT SOS")),
        ("CT", "Governor", "1", None, ("governor", "CT GOV")),
        ("WY", "US HOUSE", "", None, ("house", "WY-AL")),
        ("TX", "U.S. House", "", None, None),  # no district readable: never a fake TX-AL
        ("UT", "US HOUSE", "D1", None, None),
        ("GA", "Governor", "", None, ("governor", "GA GOV")),
        ("PA", "AUDITOR GENERAL", "NA", "STATE", ("governor", "PA AUD")),
        ("TX", "STATE SENATE", "005", "STATE", ("state", "TX SD-05")),
        ("TX", "State House", "121", None, ("state", "TX HD-121")),
        ("VT", "STATE HOUSE", "CHITTENDEN-1", "STATE", ("state", "VT LOCAL state-house-chittenden-1")),
        ("DE", "WILMINGTON CITY COUNCIL", "007", "LOCAL", ("local", "DE LOCAL new-castle-wilmington-city-council-007")),
        ("PA", "President", "", None, None),
        ("PA", "Registered Voters", "", None, None),
        # statewide offices sliced by county, and a MEDSL "LOCAL" dataverse that lies
        ("CT", "Secretary of the State", "", "STATE", ("governor", "CT SOS")),
        ("IA", "Secretary of Agriculture", "", None, ("governor", "IA AGCOM")),
        ("MN", "Governor/Lt. Governor", "STATEWIDE", None, ("governor", "MN GOV")),
        ("ND", "Governor and Lt. Governor", "", None, ("governor", "ND GOV")),
        ("MI", "STATE SENATE", "1", "LOCAL", ("state", "MI SD-01")),
        ("WA", "State House", "001, POSITION 1", "STATE", ("state", "WA HD-01A")),
        ("WA", "State Representative Pos. 2", "009", "LOCAL", ("state", "WA HD-09B")),
        ("CO", "State Representatives, District 30", "", None, ("state", "CO HD-30")),
        ("ND", "State House Unexpired 2-Year Term", "15", None, ("state", "ND HD-15 S")),
        ("ND", "State House", "4a", None, ("state", "ND HD-04A")),
        ("AK", "House District 7", "", None, ("state", "AK HD-07")),
        ("AK", "Senate District B", "", None, ("state", "AK SD-B")),
        ("AK", "State Senate", "A", None, ("state", "AK SD-A")),
        ("VT", "State Senate", "ESS-ORL", None, ("state", "VT SD-ESS-ORL")),
        ("VT", "State House", "CHT-6-7", None, ("state", "VT HD-CHT-6-7")),
        # a bare "Senate" row carries no district: it can only be the U.S. race
        ("NM", "Senate", "", None, ("senate", "NM SEN")),
    ],
)
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
    assert set(rows) == {"senate", "house", "state"}  # President skipped
    (sp,) = rows["senate"]
    assert sp["race_key"] == "GA SEN S" and sp["party"] == "REP" and sp["votes"] == 5 + 10 + 1 + 5 + 4 + 3
    assert rows["house"][0]["race_key"] == "GA-09"


def test_candidate_names_are_not_title_cased_blindly():
    """All-caps feeds go through pretty_name; mixed-case feeds keep their own casing."""
    import io

    medsl = (
        "state_po,office,district,county_name,candidate,party_simplified,mode,votes,stage\n"
        "AZ,SENATE,,Maricopa,MARTHA MCSALLY,REP,TOTAL,10,GEN\n"
        "AZ,SENATE,,Maricopa,JD VANCE,REP,TOTAL,5,GEN\n"
        "AZ,SENATE,,Maricopa,KENNEDY III,DEM,TOTAL,3,GEN\n"
    )
    names = {r["candidate"] for r in fh.aggregate_medsl(io.StringIO(medsl), "AZ").rows()["senate"]}
    assert names == {"Martha McSally", "JD Vance", "Kennedy III"}
    oe = "county,office,district,party,candidate,votes\nMaricopa,SENATE,,GRN,de la Cruz,4\n"
    assert [r["candidate"] for r in fh.aggregate_openelections(io.StringIO(oe), "AZ").rows()["senate"]] == [
        "de la Cruz"
    ]


@pytest.mark.parametrize(
    "raw,want", [("AEL", "AEL"), ("NONE", ""), ("None", ""), (None, ""), ("DEMOCRAT", "DEM"), ("GOP", "REP")]
)
def test_norm_party(raw, want):
    assert fh.norm_party(raw) == want


def test_pick_openelections_files():
    names = [
        "20201103__pa__general__county.csv",
        "20201103__pa__general__precinct.csv",
        "20200602__pa__primary__county.csv",
        "20201103__pa__special__general__state_house__1.csv",
        "20240521__ga__general__primary__county-level.csv",
        "20240618__ga__general__primary__runoff__county-level.csv",
    ]
    # both whole-state files are offered, coarsest first; the caller keeps one, never both
    assert fh.pick_openelections_files(names, "2020") == [
        ["20201103__pa__general__county.csv"],
        ["20201103__pa__general__precinct.csv"],
    ]
    assert fh.pick_openelections_files(names, "2024-primary") == [["20240521__ga__general__primary__county-level.csv"]]


def test_pick_openelections_files_partitions():
    """States that ship one file per county/town: every file of a partition, together."""
    ct = [
        "20141104__ct__general__avon__town.csv",
        "20141104__ct__general__berlin__town.csv",
        "20141104__ct__general__avon__precinct.csv",
        "20141104__ct__primary__avon__town.csv",
    ]
    assert fh.pick_openelections_files(ct, "2014") == [
        ["20141104__ct__general__avon__town.csv", "20141104__ct__general__berlin__town.csv"],
        ["20141104__ct__general__avon__precinct.csv"],
    ]
    ut = [
        "20141104__ut__general__beaver__precinct.csv",
        "20141104__ut__general__beaver__county.csv",
        "20141104__ut__general__cache__county.csv",
    ]
    assert fh.pick_openelections_files(ut, "2014") == [
        ["20141104__ut__general__beaver__county.csv", "20141104__ut__general__cache__county.csv"],
        ["20141104__ut__general__beaver__precinct.csv"],
    ]
    wi = ["20141104__wi__general__ward.csv", "20140401__wi__general__ward.csv"]
    assert fh.pick_openelections_files(wi, "2014") == [["20141104__wi__general__ward.csv"]]
    mixed = ["20141104__ct__general__andover__town.csv", "20141104__ct__general__a__precinct.csv"]
    assert fh.pick_openelections_files(mixed, "2014") == [
        ["20141104__ct__general__andover__town.csv"],
        ["20141104__ct__general__a__precinct.csv"],
    ]
    # thousands of precinct files are skipped while a county partition exists
    big = ["20201103__tx__general__county.csv"] + [
        f"20201103__tx__general__c{i}__precinct.csv" for i in range(fh.MAX_PARTITION_FILES + 1)
    ]
    assert fh.pick_openelections_files(big, "2020") == [["20201103__tx__general__county.csv"]]
    # … and a whole-state file is always offered, however many slices sit next to it
    wv = [
        "20161108__wv__general__county.csv",
        "20161108__wv__general__cabell__precinct.csv",
        "20161108__wv__general__webster__precinct.csv",
    ]
    assert fh.pick_openelections_files(wv, "2016") == [
        ["20161108__wv__general__county.csv"],
        ["20161108__wv__general__cabell__precinct.csv", "20161108__wv__general__webster__precinct.csv"],
    ]


def test_parse_openelections_name():
    # 0-2: the whole state in one file; 3+: one slice of it
    assert fh.parse_openelections_name("20141104__ct__general__avon__town.csv") == ("20141104", "general", 4)
    assert fh.parse_openelections_name("20141104__ut__general__beaver__precinct.csv") == ("20141104", "general", 6)
    assert fh.parse_openelections_name("20161108__vt__general.csv") == ("20161108", "general", 1)
    assert fh.parse_openelections_name("20161108__wv__general__county.csv") == ("20161108", "general", 0)
    assert fh.parse_openelections_name("20181106__fl__general__precinct.csv") == ("20181106", "general", 2)
    assert fh.parse_openelections_name("20181106__fl__general__alachua__precinct.csv") == ("20181106", "general", 6)
    assert fh.parse_openelections_name("20181106__fl__general-exceptional.csv") is None
    assert fh.parse_openelections_name("README.md") is None


def test_whole_state_beats_partitions():
    both = ["20201103__pa__general__county.csv", "20201103__pa__general__precinct.csv"]
    assert fh.pick_openelections_files(both, "2020") == [
        ["20201103__pa__general__county.csv"],
        ["20201103__pa__general__precinct.csv"],
    ]


def test_openelections_keeps_the_partition_with_most_races(monkeypatch, tmp_path):
    """A whole-state file that only holds the statewide offices must lose to the slice files."""
    content = {
        "20201103__pa__general__county.csv": "county,office,district,party,candidate,votes\nPA,GOVERNOR,,DEM,Tom Wolf,100\n",
        "20201103__pa__general__a__precinct.csv": "county,precinct,office,district,party,candidate,votes\nA,x,STATE HOUSE,1,DEM,Ann,10\n",
        "20201103__pa__general__b__precinct.csv": "county,precinct,office,district,party,candidate,votes\nB,x,STATE HOUSE,2,DEM,Bob,10\n",
    }

    class FakeNet:
        def __init__(self):
            self.cache = tmp_path

        def download(self, url, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content[url])
            return dest

    monkeypatch.setattr(fh, "repo_files", lambda net, repo, sub, deep=False: {n: n for n in content})
    rows, urls, note = fh.openelections(FakeNet(), "2020", "PA")
    assert note == ""
    got = rows.rows()
    assert set(got) == {"state"}  # the 2-race precinct partition won
    assert {r["race_key"] for r in got["state"]} == {"PA HD-01", "PA HD-02"}
    assert "precinct" in urls["state"] and "county.csv" not in urls["state"]


def test_repo_files_uses_git_tree(monkeypatch):
    tree = (
        "master",
        [
            "2018/20181106__fl__general__precinct.csv",
            "2018/counties/20181106__fl__general__alachua__precinct.csv",
            "2018/counties/README.txt",
            "2018/20181106__fl__general__precinct.csv.bak",
        ],
    )
    monkeypatch.setattr(fh, "git_tree", lambda net, repo: tree)
    deep = fh.repo_files(None, "openelections/openelections-data-fl", "2018", deep=True)
    assert set(deep) == {
        "20181106__fl__general__precinct.csv",
        "20181106__fl__general__precinct.csv.bak",
        "20181106__fl__general__alachua__precinct.csv",
        "README.txt",
    }
    assert deep["20181106__fl__general__alachua__precinct.csv"] == (
        "https://raw.githubusercontent.com/openelections/openelections-data-fl/master/"
        "2018/counties/20181106__fl__general__alachua__precinct.csv"
    )
    flat = fh.repo_files(None, "openelections/openelections-data-fl", "2018")
    assert list(flat) == ["20181106__fl__general__precinct.csv", "20181106__fl__general__precinct.csv.bak"]


def test_medsl_listing_needs_no_api(monkeypatch):
    """A blobless git listing replaces the rate-limited contents API."""
    tree = (
        "master",
        ["individual_states/2016-tx-precinct-general.zip", "individual_states/2016-wa-precinct-general.zip"],
    )
    monkeypatch.setattr(fh, "git_tree", lambda net, repo: tree)
    files = fh.repo_files(None, "MEDSL/2016-elections-official", "individual_states")
    assert files["2016-tx-precinct-general.zip"].startswith("https://raw.githubusercontent.com/")


def test_nyt_2024_timeline():
    doc = json.loads((FIX / "nyt_2024_pa_us_senate_trimmed.json").read_text())
    lines = fh.nyt_timeline_2024(doc, "PA")["PA SEN"]
    assert [x["total_votes"] for x in lines] == [5871429, 6963137]  # 0-vote point dropped
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


def test_history_text_falls_back_to_cp1252(tmp_path):
    from tools.fetch_history import _iter_text

    f = tmp_path / "in.csv"
    f.write_bytes("district,candidate\n7,André Carson\n".encode("cp1252"))
    assert "André Carson" in "".join(_iter_text(f))
