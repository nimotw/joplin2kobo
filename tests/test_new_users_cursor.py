"""規格 docs/specs/T44-new-users-cursor.md 的驗收條件測試（編號對應規格）。"""
import json
import sys
from datetime import datetime, timedelta

import pytest

import news_digest as nd
from test_new_users import device, iso, stats

TZ = nd.TZ


@pytest.fixture
def env(tmp_path, monkeypatch):
    """假的 Joplin／新聞來源。回傳可設定的狀態與 run()。"""
    st = {"created": [], "digest_time": None, "digest_calls": 0, "fail_create": False}
    cursor = tmp_path / "new_users_cursor.json"

    def fake_last_digest_time():
        st["digest_calls"] += 1
        return st["digest_time"]

    def fake_create(title, body, fid):
        if st["fail_create"]:
            raise RuntimeError("joplin down")
        st["created"].append(body)
        return "nid"

    monkeypatch.setattr(nd, "API_URL", "http://joplin.invalid")
    monkeypatch.setattr(nd, "API_TOKEN", "t")
    monkeypatch.setattr(nd, "INBOX", "kobo")
    monkeypatch.setattr(nd, "STATE_FILE", str(tmp_path / "pending.json"))
    monkeypatch.setattr(nd, "previous_digests", lambda since: (set(), []))
    monkeypatch.setattr(nd, "last_digest_time", fake_last_digest_time)
    monkeypatch.setattr(nd, "get_notebook_id_by_name", lambda *a: "folder")
    monkeypatch.setattr(nd, "create_note", fake_create)
    monkeypatch.setattr(nd, "collect", lambda now, k, t: [])
    monkeypatch.delenv("NEWS_NEW_USERS_CURSOR", raising=False)

    def run(*args, data=None, enabled=True):
        """data：統計檔內容（dict 或字串）；None 表示檔案不存在。"""
        path = tmp_path / "new-users.json"
        if data is not None:
            path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False),
                            encoding="utf-8")
        elif path.exists():
            path.unlink()
        monkeypatch.setattr(nd, "NEW_USERS_FILE", str(path) if enabled else "")
        monkeypatch.setattr(sys, "argv", ["news_digest.py", *args])
        return nd.main()

    st["run"] = run
    st["cursor"] = cursor
    return st


def now():
    return datetime.now(TZ).replace(microsecond=0)


def write_cursor(env, generated=None, raw=None):
    """寫合法游標（generated）或任意原始內容（raw）。"""
    content = raw if raw is not None else json.dumps({"generated": generated})
    env["cursor"].write_text(content, encoding="utf-8")


def read_cursor(env):
    return json.loads(env["cursor"].read_text(encoding="utf-8"))["generated"]


# ---------- 起點 ----------

def test_01_cursor_is_used_as_since(env):
    n = now()
    write_cursor(env, iso(n - timedelta(hours=2)))
    env["digest_time"] = n - timedelta(hours=1)     # 若誤用摘要時間，會把 90 分鐘前的裝置濾掉
    data = stats(devices=[device(n - timedelta(minutes=90), county="台北市"),
                          device(n - timedelta(hours=3), county="高雄市")], generated=n)
    assert env["run"]("--publish", data=data) == 0
    body = env["created"][0]
    assert "台北市" in body and "高雄市" not in body
    assert env["digest_calls"] == 0


def test_02_missing_cursor_falls_back_to_last_digest(env):
    n = now()
    env["digest_time"] = n - timedelta(hours=1)
    data = stats(devices=[device(n - timedelta(minutes=30), county="台北市"),
                          device(n - timedelta(hours=2), county="高雄市")], generated=n)
    env["run"]("--publish", data=data)
    body = env["created"][0]
    assert "台北市" in body and "高雄市" not in body
    assert env["digest_calls"] == 1


@pytest.mark.parametrize("content", ["{not json", json.dumps({"x": 1}), json.dumps({"generated": "soon"})])
def test_03_broken_cursor_falls_back_with_warning(env, capsys, content):
    n = now()
    write_cursor(env, raw=content)
    env["digest_time"] = n - timedelta(hours=1)
    data = stats(devices=[device(n - timedelta(minutes=30), county="台北市"),
                          device(n - timedelta(hours=2), county="高雄市")], generated=n)
    env["run"]("--publish", data=data)
    body = env["created"][0]
    assert "台北市" in body and "高雄市" not in body
    assert env["digest_calls"] == 1
    assert "游標" in capsys.readouterr().err


# ---------- 更新游標 ----------

def test_04_cursor_saved_after_successful_note(env):
    n = now()
    gen = iso(n)
    data = dict(stats(devices=[device(n - timedelta(minutes=30))]), generated=gen)
    env["run"]("--publish", data=data)
    assert read_cursor(env) == gen
    assert not (env["cursor"].parent / "new_users_cursor.json.tmp").exists()


def test_04_cursor_saved_even_with_zero_new_users(env, monkeypatch):
    n = now()
    a = nd.Article("某則新聞", "https://example.com/a", "中央社", n)
    monkeypatch.setattr(nd, "collect", lambda now, k, t: [("社會", a)])
    env["digest_time"] = n
    gen = iso(n)
    env["run"]("--publish", data=dict(stats(devices=[device(n - timedelta(days=1))]), generated=gen))
    assert len(env["created"]) == 1 and "新使用者" not in env["created"][0]
    assert read_cursor(env) == gen


def test_04_cursor_path_follows_env(env, tmp_path, monkeypatch):
    custom = tmp_path / "sub" / "c.json"
    monkeypatch.setenv("NEWS_NEW_USERS_CURSOR", str(custom))
    n = now()
    env["run"]("--publish", data=stats(devices=[device(n)], generated=n))
    assert json.loads(custom.read_text(encoding="utf-8"))["generated"] == iso(n)
    assert not env["cursor"].exists()


def test_05_dry_run_does_not_save(env):
    n = now()
    env["run"]("--dry-run", data=stats(devices=[device(n)], generated=n))
    assert not env["cursor"].exists()


def test_05_collect_does_not_save(env):
    n = now()
    env["run"]("--collect", data=stats(devices=[device(n)], generated=n))
    assert not env["cursor"].exists()


def test_05_missing_or_stale_file_does_not_save(env, monkeypatch):
    n = now()
    a = nd.Article("某則新聞", "https://example.com/a", "中央社", n)
    monkeypatch.setattr(nd, "collect", lambda now, k, t: [("社會", a)])
    env["run"]("--publish", data=None)
    env["run"]("--publish", data=stats(devices=[device(n)], generated=n - timedelta(days=1)))
    assert len(env["created"]) == 2
    assert not env["cursor"].exists()


def test_05_disabled_feature_neither_reads_nor_writes_cursor(env, monkeypatch):
    n = now()
    write_cursor(env, raw="{not json")
    a = nd.Article("某則新聞", "https://example.com/a", "中央社", n)
    monkeypatch.setattr(nd, "collect", lambda now, k, t: [("社會", a)])
    env["run"]("--publish", data=stats(devices=[device(n)], generated=n), enabled=False)
    assert env["cursor"].read_text(encoding="utf-8") == "{not json"
    assert env["digest_calls"] == 0


def test_05_no_note_does_not_save(env):
    n = now()
    env["digest_time"] = n
    env["run"]("--publish", data=stats(devices=[device(n - timedelta(days=1))], generated=n))
    assert env["created"] == []
    assert not env["cursor"].exists()


def test_05_failed_note_does_not_save(env):
    n = now()
    env["fail_create"] = True
    with pytest.raises(RuntimeError):
        env["run"]("--publish", data=stats(devices=[device(n)], generated=n))
    assert not env["cursor"].exists()


# ---------- 情境 ----------

def test_06_rerun_same_file_lists_nothing_again(env, monkeypatch):
    n = now()
    data = stats(devices=[device(n - timedelta(minutes=5), county="台北市")], generated=n)
    env["run"]("--publish", data=data)
    a = nd.Article("某則新聞", "https://example.com/a", "中央社", n)
    monkeypatch.setattr(nd, "collect", lambda now, k, t: [("社會", a)])
    env["run"]("--publish", data=data)
    assert "台北市" in env["created"][0]
    assert "新使用者" not in env["created"][1]


def test_07_item_between_generation_and_digest_is_not_lost(env):
    n = now()
    yesterday_gen = n - timedelta(days=1, minutes=20)            # 前一天 05:30
    yesterday_digest = n - timedelta(days=1)                     # 前一天 05:50
    write_cursor(env, iso(yesterday_gen))
    env["digest_time"] = yesterday_digest
    data = stats(devices=[device(yesterday_gen + timedelta(minutes=10), county="嘉義縣")], generated=n)
    env["run"]("--publish", data=data)
    assert "- 新裝置 1 台：嘉義縣" in env["created"][0]
