"""規格 docs/specs/T42-new-users-section.md 的驗收條件測試（編號對應規格）。"""
import json
import sys
from datetime import datetime, timedelta

import pytest

import news_digest as nd

TZ = nd.TZ
NOW = datetime(2026, 11, 22, 5, 50, tzinfo=TZ)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def device(first_seen: datetime, county="嘉義縣", user="secretuser", dev="22e662f1", points=1234):
    return {"user": user, "device": dev, "firstSeen": iso(first_seen), "county": county, "points": points}


def link(first_seen: datetime, opens=10, countries=("TW",), hash_="56b026e5",
         label="群組 Forest-18D9DD2D（dany/0cf63f36, conifer/e1ccae3b）"):
    return {"hash": hash_, "kind": "group", "label": label, "firstSeen": iso(first_seen),
            "opens": opens, "countries": list(countries)}


def stats(devices=(), links=(), generated=NOW - timedelta(minutes=20), version=1):
    return {"version": version, "generated": iso(generated), "windowDays": 7,
            "devices": list(devices), "links": list(links)}


def write(tmp_path, data) -> str:
    p = tmp_path / "new-users.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ---------- 篩選 ----------

def test_01_first_seen_must_be_strictly_after_last_digest():
    since = NOW - timedelta(days=1)
    data = stats(devices=[device(since), device(since + timedelta(seconds=1), county="台北市")],
                 links=[link(since), link(since + timedelta(minutes=1), opens=5)])
    devices, links = nd.filter_new_users(data, since)
    assert [d["county"] for d in devices] == ["台北市"]
    assert [l["opens"] for l in links] == [5]


def test_02_no_previous_digest_means_everything_is_new():
    data = stats(devices=[device(NOW - timedelta(days=6))], links=[link(NOW - timedelta(days=5))])
    devices, links = nd.filter_new_users(data, None)
    assert len(devices) == 1 and len(links) == 1


# ---------- 輸出 ----------

def test_04_devices_line_counties_in_order_with_counts():
    t = NOW - timedelta(hours=5)
    devs = [device(t, "嘉義縣"), device(t + timedelta(minutes=1), "台灣以外"),
            device(t + timedelta(minutes=2), "嘉義縣"), device(t + timedelta(minutes=3), None)]
    md = nd.render_new_users(devs, [])
    assert "- 新裝置 4 台：嘉義縣（2）、台灣以外、未知" in md


def test_05_links_line_sums_opens_and_unions_countries():
    t = NOW - timedelta(hours=5)
    md = nd.render_new_users([], [link(t, opens=100, countries=["TW", "JP"]),
                                  link(t, opens=22, countries=["US", "TW"])])
    assert "- 新連結 2 個：被開啟 122 次（JP、TW、US）" in md


def test_05_links_line_without_countries_has_no_parentheses():
    md = nd.render_new_users([], [link(NOW, opens=3, countries=[])])
    line = [l for l in md.splitlines() if l.startswith("- 新連結")][0]
    assert line == "- 新連結 1 個：被開啟 3 次"


def test_06_zero_lines_and_empty_section_are_omitted():
    only_dev = nd.render_new_users([device(NOW)], [])
    assert "## 新使用者" in only_dev and "新連結" not in only_dev
    only_link = nd.render_new_users([], [link(NOW)])
    assert "## 新使用者" in only_link and "新裝置" not in only_link
    assert nd.render_new_users([], []) == ""


def test_07_no_names_ids_hashes_or_labels_in_output():
    d = device(NOW, user="alice-private", dev="deadbeef")
    l = link(NOW, hash_="cafebabe", label="群組 Secret（bob/1234abcd）")
    md = nd.render_new_users([d], [l])
    for secret in ("alice-private", "deadbeef", "cafebabe", "Secret", "bob", "1234abcd"):
        assert secret not in md


def test_section_is_placed_after_header_and_before_news():
    a = nd.Article("某則新聞", "https://example.com/a", "中央社", NOW - timedelta(hours=1))
    body = nd.render(NOW, [("社會", a)], nd.render_new_users([device(NOW)], []))
    lines = body.splitlines()
    assert "產生，共 1 則" in lines[0]
    assert body.index("## 新使用者") < body.index("## 社會")


def test_render_without_section_is_unchanged():
    a = nd.Article("某則新聞", "https://example.com/a", "中央社", NOW - timedelta(hours=1))
    assert nd.render(NOW, [("社會", a)], "") == nd.render(NOW, [("社會", a)])


# ---------- 讀檔與錯誤 ----------

def test_load_valid_file(tmp_path):
    data = stats(devices=[device(NOW)])
    assert nd.load_new_users(write(tmp_path, data), NOW) == data


def test_10_missing_file_returns_none_with_warning(tmp_path, capsys):
    assert nd.load_new_users(str(tmp_path / "nope.json"), NOW) is None
    assert "新使用者" in capsys.readouterr().err


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps({"generated": iso(NOW), "devices": [], "links": []}),          # 缺 version
    json.dumps({"version": 1, "devices": [], "links": []}),                   # 缺 generated
    json.dumps({"version": 1, "generated": iso(NOW), "links": []}),           # 缺 devices
    json.dumps({"version": 1, "generated": iso(NOW), "devices": []}),         # 缺 links
])
def test_11_invalid_json_or_missing_keys(tmp_path, capsys, content):
    assert nd.load_new_users(write(tmp_path, content), NOW) is None
    assert capsys.readouterr().err.strip()


def test_12_unknown_version(tmp_path, capsys):
    assert nd.load_new_users(write(tmp_path, stats(version=2)), NOW) is None
    assert capsys.readouterr().err.strip()


def test_13_stale_file_from_another_day(tmp_path, capsys):
    old = stats(generated=NOW - timedelta(days=1))
    assert nd.load_new_users(write(tmp_path, old), NOW) is None
    assert capsys.readouterr().err.strip()


def test_14_unparseable_first_seen_is_skipped():
    bad = device(NOW)
    bad["firstSeen"] = "yesterday-ish"
    data = stats(devices=[bad, device(NOW, county="台北市")])
    devices, _ = nd.filter_new_users(data, NOW - timedelta(days=1))
    assert [d["county"] for d in devices] == ["台北市"]


# ---------- 主流程 ----------

@pytest.fixture
def run_main(tmp_path, monkeypatch):
    """以假的 Joplin / 新聞來源跑 main()，回傳 (結束碼, 建立的筆記清單, load_new_users 是否被呼叫)。"""
    created = []
    calls = {"load": 0}
    real_load = nd.load_new_users

    def fake_load(path, now):
        calls["load"] += 1
        return real_load(path, now)

    monkeypatch.setattr(nd, "API_URL", "http://joplin.invalid")
    monkeypatch.setattr(nd, "API_TOKEN", "t")
    monkeypatch.setattr(nd, "INBOX", "kobo")
    monkeypatch.setattr(nd, "STATE_FILE", str(tmp_path / "pending.json"))
    monkeypatch.setattr(nd, "previous_digests", lambda since: (set(), []))
    monkeypatch.setattr(nd, "last_digest_time", lambda: None)
    monkeypatch.setattr(nd, "get_notebook_id_by_name", lambda *a: "folder")
    monkeypatch.setattr(nd, "create_note", lambda title, body, fid: created.append((title, body)) or "nid")
    monkeypatch.setattr(nd, "load_new_users", fake_load)

    def run(*args, news=(), new_users=None):
        monkeypatch.setattr(nd, "collect", lambda now, k, t: list(news))
        if new_users is None:
            monkeypatch.setattr(nd, "NEW_USERS_FILE", "")
        else:
            data = dict(new_users, generated=iso(datetime.now(TZ)))
            monkeypatch.setattr(nd, "NEW_USERS_FILE", write(tmp_path, data))
        monkeypatch.setattr(sys, "argv", ["news_digest.py", *args])
        return nd.main(), created, calls["load"]
    return run


def test_03_collect_mode_does_not_read_new_users_file(run_main):
    code, created, loads = run_main("--collect", new_users=stats(devices=[device(NOW)]))
    assert code == 0 and created == [] and loads == 0


def test_08_publishes_with_only_new_users(run_main):
    code, created, _ = run_main("--publish", new_users=stats(devices=[device(NOW)]))
    assert code == 0 and len(created) == 1
    body = created[0][1]
    assert "產生，共 0 則" in body.splitlines()[0]
    assert "## 新使用者" in body and "- 新裝置 1 台：嘉義縣" in body


def test_09_nothing_new_creates_no_note(run_main):
    code, created, _ = run_main("--publish", new_users=stats())
    assert code == 0 and created == []


def test_disabled_feature_does_not_read_or_warn(run_main, capsys):
    a = nd.Article("某則新聞", "https://example.com/a", "中央社", datetime.now(TZ))
    code, created, loads = run_main("--publish", news=[("社會", a)])
    assert code == 0 and loads == 0 and len(created) == 1
    assert "新使用者" not in created[0][1]
    assert "新使用者" not in capsys.readouterr().err
