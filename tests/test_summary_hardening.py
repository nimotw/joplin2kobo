"""規格 docs/specs/T64-summary-hardening.md 的驗收條件（編號對應規格）。"""
import json
from datetime import datetime, timedelta

import pytest

import digest_summary as ds
from test_digest_summary import NOW, TZ, body, claude_stdout, env, result  # noqa: F401  (env 是 fixture)


def at(h, m, s=0):
    return NOW.replace(hour=h, minute=m, second=s)


# ---------- 第 1 項：找不到週報要重試，發布日找不到要報錯 ----------

def test_01_not_publish_day_no_retry(env, monkeypatch):
    monkeypatch.setattr(ds, "PUBLISH_WEEKDAY", 3, raising=False)
    env["note"] = None
    assert env["run"]() == 0
    assert env["finds"] == 1 and env["sleeps"] == [] and env["claude_calls"] == []


def test_02_publish_day_found_on_second_try(env):
    note = env["note"]
    env["note"] = lambda st: note if st["finds"] >= 2 else None
    env["claude"] = (0, claude_stdout(result()))
    assert env["run"]() == 0
    assert env["sleeps"] == [30] and len(env["updated"]) == 1


def test_03_publish_day_never_found(env, capsys):
    env["note"] = None
    assert env["run"]() == 1
    assert env["clock"] >= at(5, 58) and env["clock"] < at(5, 58, 30)
    assert all(s == 30 for s in env["sleeps"]) and env["claude_calls"] == []
    assert capsys.readouterr().err.strip()


def test_04_empty_weekday_means_every_day(env, monkeypatch):
    monkeypatch.setattr(ds, "PUBLISH_WEEKDAY", None, raising=False)
    env["note"] = None
    assert env["run"]() == 1
    assert env["sleeps"]


def test_05_timeout_shrinks_to_deadline(env):
    env["clock"] = at(5, 57)
    env["claude"] = (0, claude_stdout(result()))
    assert env["run"]() == 0
    assert env["claude_calls"][0][1]["timeout"] == 150


def test_06_too_late_to_call_claude(env, capsys):
    env["clock"] = at(5, 58, 40)
    env["claude"] = (0, claude_stdout(result()))
    assert env["run"]() == 1
    assert env["claude_calls"] == [] and env["updated"] == []
    assert capsys.readouterr().err.strip()


def test_13_manual_run_in_evening_has_no_deadline(env):
    env["clock"] = at(23, 27)
    env["claude"] = (0, claude_stdout(result()))
    assert env["run"]() == 0
    assert env["claude_calls"][0][1]["timeout"] == ds.TIMEOUT


def test_14_manual_run_after_six_not_found_no_retry(env):
    env["clock"] = at(6, 10)
    env["note"] = None
    assert env["run"]() == 1
    assert env["sleeps"] == [] and env["finds"] == 1


# ---------- 第 3 項：模型文字不能帶連結或 HTML ----------

@pytest.mark.parametrize("bad", ["<img src=x>", "a > b", "[點我](x)", "見 http://evil", "見 https://evil",
                                 "www.evil.com"])
def test_08_text_with_links_or_html_fails(env, capsys, bad):
    env["claude"] = (0, claude_stdout(result(taiwan=f"台灣那段 {bad}")))
    assert env["run"]() == 1
    assert env["updated"] == []
    assert capsys.readouterr().err.strip()


def test_09_text_collapsed_into_one_paragraph_without_heading(env):
    env["claude"] = (0, claude_stdout(result(world="## 假標題\n\n第二段")))
    assert env["run"]() == 0
    out = env["updated"][0][1]
    assert "假標題 第二段" in out
    assert "## 假標題" not in out


# ---------- 第 6 項：解析筆數跟開頭那行不符要警告 ----------

def test_10_unparsable_item_warns_with_both_counts(env, capsys):
    env["note"]["body"] = body().replace("https://cna.example/2", "https://cna.example/a b")
    env["claude"] = (0, claude_stdout(result(tr=())))
    assert env["run"]() == 0 and len(env["updated"]) == 1
    err = capsys.readouterr().err
    warn = [l for l in err.splitlines() if "⚠️" in l and "共 3 則" in l and "2 則" in l]
    assert warn


def test_11_matching_count_no_warning(env, capsys):
    env["claude"] = (0, claude_stdout(result()))
    env["run"]()
    err = capsys.readouterr().err
    assert not [l for l in err.splitlines() if "⚠️" in l]


# ---------- 第 7 項：INBOX 找不到時不建立筆記本 ----------

class FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_12_missing_inbox_exits_1_without_creating(monkeypatch, capsys):
    posts = []
    monkeypatch.setattr(ds.nd, "API_URL", "http://joplin.invalid")
    monkeypatch.setattr(ds.nd, "API_TOKEN", "t")
    monkeypatch.setattr(ds.nd, "INBOX", "kobo-typo")
    monkeypatch.setattr(ds, "_now", lambda: NOW, raising=False)
    monkeypatch.setattr(ds, "_sleep", lambda s: None, raising=False)

    def fake_get(url, params=None, **kw):
        if url.endswith("/folders"):
            first = (params or {}).get("page", 1) == 1
            items = [{"id": "f1", "title": "kobo"}, {"id": "f2", "title": "2026"}] if first else []
            return FakeResp({"items": items, "has_more": False})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(ds.requests, "get", fake_get)
    monkeypatch.setattr(ds.requests, "post", lambda *a, **k: posts.append(a) or FakeResp({"id": "new"}))
    # note2read 的 get_notebook_id_by_name 也走 requests；兩邊都擋
    import note2read
    monkeypatch.setattr(note2read.requests, "get", fake_get)
    monkeypatch.setattr(note2read.requests, "post", lambda *a, **k: posts.append(a) or FakeResp({"id": "new"}))
    assert ds.main(NOW) == 1
    assert posts == []
    assert "kobo-typo" in capsys.readouterr().err


# ---------- 第 2 項：去重標題清單不重複（news_digest） ----------

def test_07_seen_titles_have_no_duplicates(tmp_path, monkeypatch):
    import sys
    import news_digest as nd
    got = {}
    pub = tmp_path / "news_published.json"
    pub.write_text(json.dumps([{"key": "k1", "norm": "dup", "date": datetime.now(TZ).isoformat()}]),
                   encoding="utf-8")
    monkeypatch.setattr(nd, "API_URL", "http://joplin.invalid")
    monkeypatch.setattr(nd, "API_TOKEN", "t")
    monkeypatch.setattr(nd, "STATE_FILE", str(tmp_path / "pending.json"))
    monkeypatch.setattr(nd, "previous_digests", lambda since: ({"k1"}, ["dup", "other", "dup"]))
    monkeypatch.setattr(nd, "collect", lambda n, k, t: got.setdefault("titles", list(t)) and [])
    monkeypatch.setattr(sys, "argv", ["news_digest.py", "--collect"])
    assert nd.main() == 0
    assert got["titles"].count("dup") == 1
    assert got["titles"] == ["dup", "other"]
