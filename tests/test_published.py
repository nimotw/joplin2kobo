"""規格 docs/specs/T48-digest-ai-summary.md 驗收條件 10–12（news_digest.py 發布紀錄）。"""
import json
import sys
from datetime import datetime, timedelta

import pytest

import news_digest as nd

TZ = nd.TZ


def now():
    return datetime.now(TZ).replace(microsecond=0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    st = {"created": [], "fail_create": False, "collected_with": None,
          "published": tmp_path / "news_published.json", "news": []}

    def fake_create(title, body, fid):
        if st["fail_create"]:
            raise RuntimeError("joplin down")
        st["created"].append(body)
        return "nid"

    def fake_collect(n, keys, titles):
        st["collected_with"] = (set(keys), list(titles))
        return list(st["news"])

    monkeypatch.setattr(nd, "API_URL", "http://joplin.invalid")
    monkeypatch.setattr(nd, "API_TOKEN", "t")
    monkeypatch.setattr(nd, "INBOX", "kobo")
    monkeypatch.setattr(nd, "STATE_FILE", str(tmp_path / "pending.json"))
    monkeypatch.setattr(nd, "NEW_USERS_FILE", "")
    monkeypatch.setattr(nd, "previous_digests", lambda since: ({"note-key"}, ["note title"]))
    monkeypatch.setattr(nd, "get_notebook_id_by_name", lambda *a: "folder")
    monkeypatch.setattr(nd, "create_note", fake_create)
    monkeypatch.setattr(nd, "collect", fake_collect)
    monkeypatch.delenv("NEWS_PUBLISHED_FILE", raising=False)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["news_digest.py", *args])
        return nd.main()

    st["run"] = run
    return st


def a(title, link, n=None):
    return nd.Article(title, link, "中央社", n or now())


def read(env):
    return json.loads(env["published"].read_text(encoding="utf-8"))


# ---------- 10 ----------

def test_10_publish_records_every_item(env):
    env["news"] = [("社會", a("新聞一", "https://x.example/1")), ("國際", a("新聞二", "https://x.example/2"))]
    assert env["run"]("--publish") == 0
    rec = read(env)
    keys = {r["key"] for r in rec}
    assert keys == {nd.url_key("https://x.example/1"), nd.url_key("https://x.example/2")}
    assert {r["norm"] for r in rec} == {nd.norm_title("新聞一"), nd.norm_title("新聞二")}
    assert not env["published"].with_name("news_published.json.tmp").exists()


def test_10_old_records_are_dropped_and_new_appended(env):
    n = now()
    old = (n - timedelta(days=nd.DEDUP_DAYS + 1)).isoformat()
    recent = (n - timedelta(days=1)).isoformat()
    env["published"].write_text(json.dumps([
        {"key": "old", "norm": "old", "date": old},
        {"key": "recent", "norm": "recent", "date": recent},
    ]), encoding="utf-8")
    env["news"] = [("社會", a("新聞一", "https://x.example/1"))]
    env["run"]("--publish")
    keys = {r["key"] for r in read(env)}
    assert keys == {"recent", nd.url_key("https://x.example/1")}


def test_10_path_follows_env(env, tmp_path, monkeypatch):
    custom = tmp_path / "sub" / "p.json"
    monkeypatch.setenv("NEWS_PUBLISHED_FILE", str(custom))
    env["news"] = [("社會", a("新聞一", "https://x.example/1"))]
    env["run"]("--publish")
    assert custom.exists() and not env["published"].exists()


# ---------- 11 ----------

@pytest.mark.parametrize("mode", ["--dry-run", "--collect"])
def test_11_dry_run_and_collect_do_not_record(env, mode):
    env["news"] = [("社會", a("新聞一", "https://x.example/1"))]
    env["run"](mode)
    assert not env["published"].exists()


def test_11_no_note_does_not_record(env):
    env["run"]("--publish")
    assert env["created"] == [] and not env["published"].exists()


def test_11_failed_note_does_not_record(env):
    env["news"] = [("社會", a("新聞一", "https://x.example/1"))]
    env["fail_create"] = True
    with pytest.raises(RuntimeError):
        env["run"]("--publish")
    assert not env["published"].exists()


# ---------- 12 ----------

def test_12_dedup_uses_published_records_and_notes(env):
    env["published"].write_text(json.dumps([
        {"key": "rec-key", "norm": "rec title", "date": now().isoformat()}]), encoding="utf-8")
    env["run"]("--collect")
    keys, titles = env["collected_with"]
    assert {"rec-key", "note-key"} <= keys
    assert "rec title" in titles and "note title" in titles


def test_12_missing_record_uses_notes_only(env, capsys):
    env["run"]("--collect")
    keys, titles = env["collected_with"]
    assert "note-key" in keys and "note title" in titles
    assert "發布紀錄" not in capsys.readouterr().err


@pytest.mark.parametrize("content", ["{not json", json.dumps({"a": 1}), json.dumps([{"key": 1}])])
def test_12_broken_record_warns_and_uses_notes(env, capsys, content):
    env["published"].write_text(content, encoding="utf-8")
    assert env["run"]("--collect") == 0
    keys, _ = env["collected_with"]
    assert "note-key" in keys
    assert "發布紀錄" in capsys.readouterr().err
