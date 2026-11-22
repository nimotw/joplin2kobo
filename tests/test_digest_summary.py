"""規格 docs/specs/T48-digest-ai-summary.md 驗收條件 1–9（digest_summary.py）。"""
import json
import subprocess
from datetime import datetime, timedelta

import pytest

import digest_summary as ds
import news_digest as nd

TZ = nd.TZ
NOW = datetime(2026, 11, 23, 5, 51, tzinfo=TZ)


def art(title, link, source="中央社", minutes=60, summary=""):
    return nd.Article(title, link, source, NOW - timedelta(minutes=minutes), summary)


ITEMS = [
    ("社會", art("彰化[甘澍宮]大火", "https://cna.example/1", summary="近百年廟宇正殿燒毀")),
    ("財經", art("台股一週跌逾7600點", "https://cna.example/2", minutes=30)),
    ("國際", art("俄飛彈落波蘭", "https://pts.example/3", source="公視", summary="北約軍演期間")),
]
NEW_USERS = "## 新使用者\n\n- 新裝置 1 台：嘉義縣\n"


def body(new_users=NEW_USERS):
    return nd.render(NOW, ITEMS, new_users)


def result(world="全世界那段", taiwan="全台灣那段", local="少數人那段",
           wr=(3,), tr=(2,), lr=(1,)):
    return {"world": {"text": world, "refs": list(wr)},
            "taiwan": {"text": taiwan, "refs": list(tr)},
            "local": {"text": local, "refs": list(lr)}}


def claude_stdout(inner, is_error=False):
    text = inner if isinstance(inner, str) else json.dumps(inner, ensure_ascii=False)
    return json.dumps({"type": "result", "is_error": is_error, "result": text}, ensure_ascii=False)


# ---------- 3 解析 ----------

def test_03_parse_items_head_and_new_users():
    d = ds.parse_digest(body())
    assert d["head"] == body().splitlines()[0]
    assert d["new_users"].strip() == NEW_USERS.strip()
    by_url = {i["url"]: i for i in d["items"]}
    assert set(by_url) == {"https://cna.example/1", "https://cna.example/2", "https://pts.example/3"}
    fire = by_url["https://cna.example/1"]
    assert fire["title"] == "彰化[甘澍宮]大火"
    assert fire["section"] == "社會"
    assert fire["meta"].startswith("中央社 · ")
    assert fire["summary"] == "近百年廟宇正殿燒毀"
    assert [i["n"] for i in d["items"]] == [1, 2, 3]


def test_03_parse_without_new_users():
    d = ds.parse_digest(body(new_users=""))
    assert d["new_users"] == "" and len(d["items"]) == 3


# ---------- 4 prompt ----------

def test_04_prompt_has_numbers_sections_titles_summaries_but_no_urls():
    d = ds.parse_digest(body())
    p = ds.build_prompt(d["items"])
    for i in d["items"]:
        assert f"{i['n']}." in p or f"[{i['n']}]" in p
        assert i["title"] in p and i["section"] in p
    assert "近百年廟宇正殿燒毀" in p
    assert "http" not in p


# ---------- 5、6 輸出 ----------

def test_05_render_layout():
    d = ds.parse_digest(body())
    out = ds.render_summary(d, result())
    lines = out.splitlines()
    assert lines[0] == d["head"]
    assert out.index("## 新使用者") < out.index("## 影響全世界") < out.index("## 影響全台灣") < out.index("## 影響少數人")
    world = out[out.index("## 影響全世界"):out.index("## 影響全台灣")]
    assert "全世界那段" in world and "(https://pts.example/3)" in world
    assert "https://cna.example/1" not in world
    local = out[out.index("## 影響少數人"):]
    assert "[彰化\\[甘澍宮\\]大火](https://cna.example/1)" in local
    assert "近百年廟宇正殿燒毀" not in out          # 連結底下不放簡介
    assert out.rstrip().endswith(ds.MARKER)


def test_05_empty_section_is_omitted():
    d = ds.parse_digest(body(new_users=""))
    out = ds.render_summary(d, result(world="", wr=()))
    assert "影響全世界" not in out and "## 新使用者" not in out
    assert "## 影響全台灣" in out


def test_05_refs_listed_in_number_order():
    d = ds.parse_digest(body())
    out = ds.render_summary(d, result(world="w", wr=(3, 1)))
    world = out[out.index("## 影響全世界"):out.index("## 影響全台灣")]
    assert world.index("cna.example/1") < world.index("pts.example/3")


def test_06_invalid_refs_are_ignored():
    d = ds.parse_digest(body())
    out = ds.render_summary(d, result(wr=(3, 3, 0, 99, "2", 1.5)))
    world = out[out.index("## 影響全世界"):out.index("## 影響全台灣")]
    assert world.count("pts.example/3") == 1
    assert "cna.example/2" not in world


def test_06_text_kept_when_all_refs_invalid():
    d = ds.parse_digest(body())
    out = ds.render_summary(d, result(wr=(99,)))
    world = out[out.index("## 影響全世界"):out.index("## 影響全台灣")]
    assert "全世界那段" in world and "](" not in world


# ---------- 主流程 ----------

@pytest.fixture
def env(monkeypatch):
    st = {"note": {"id": "n1", "title": "新聞摘要 2026-11-16 ~ 11-23", "body": body()},
          "in_inbox_after": True, "updated": [], "claude_calls": [], "claude": None,
          "clock": NOW, "sleeps": [], "finds": 0}

    # T64：時鐘與 sleep 可控（raising=False：舊程式還沒有這兩個函式）
    def fake_sleep(sec):
        st["sleeps"].append(sec)
        st["clock"] += timedelta(seconds=sec)

    monkeypatch.setattr(ds, "_now", lambda: st["clock"], raising=False)
    monkeypatch.setattr(ds, "_sleep", fake_sleep, raising=False)
    monkeypatch.setattr(ds, "PUBLISH_WEEKDAY", 0, raising=False)   # NOW 是週一 = 發布日

    def fake_find(now):
        st["finds"] += 1
        note = st["note"]
        return note(st) if callable(note) else note

    monkeypatch.setattr(ds, "find_digest", fake_find)
    monkeypatch.setattr(ds, "still_in_inbox", lambda note_id: st["in_inbox_after"])
    monkeypatch.setattr(ds, "update_body", lambda note_id, b: st["updated"].append((note_id, b)))

    def fake_run(args, **kw):
        st["claude_calls"].append((args, kw))
        r = st["claude"]
        if isinstance(r, Exception):
            raise r
        code, out = r
        return subprocess.CompletedProcess(args, code, stdout=out, stderr="boom" if code else "")

    monkeypatch.setattr(ds.subprocess, "run", fake_run)
    st["run"] = lambda: ds.main(st["clock"])
    return st


def test_01_no_digest_today(env, monkeypatch):
    monkeypatch.setattr(ds, "PUBLISH_WEEKDAY", 3, raising=False)    # 今天不是發布日
    env["note"] = None
    assert env["run"]() == 0
    assert env["claude_calls"] == [] and env["updated"] == []


def test_02_already_summarized(env):
    env["note"]["body"] += "\n" + ds.MARKER + "\n"
    assert env["run"]() == 0
    assert env["claude_calls"] == [] and env["updated"] == []


def test_05_main_rewrites_note(env):
    env["claude"] = (0, claude_stdout(result()))
    assert env["run"]() == 0
    assert len(env["updated"]) == 1
    note_id, new_body = env["updated"][0]
    assert note_id == "n1" and "## 影響全世界" in new_body and new_body.rstrip().endswith(ds.MARKER)


def test_05_json_inside_code_fence_is_accepted(env):
    env["claude"] = (0, claude_stdout("```json\n" + json.dumps(result(), ensure_ascii=False) + "\n```"))
    assert env["run"]() == 0 and len(env["updated"]) == 1


@pytest.mark.parametrize("claude", [
    subprocess.TimeoutExpired(cmd="claude", timeout=420),
    (1, ""),
    (0, claude_stdout(result(), is_error=True)),
    (0, "not json at all"),
    (0, claude_stdout("我無法完成")),
    (0, claude_stdout({"world": {"text": "a", "refs": []}, "taiwan": {"text": "b", "refs": []}})),
    (0, claude_stdout(result(world=123))),
    (0, claude_stdout(result(world="", taiwan="", local="", wr=(), tr=(), lr=()))),
], ids=["timeout", "exit1", "is_error", "outer-not-json", "inner-not-json", "missing-key",
        "text-not-str", "all-empty"])
def test_07_failures_leave_note_untouched(env, capsys, claude):
    env["claude"] = claude
    assert env["run"]() != 0
    assert env["updated"] == []
    assert capsys.readouterr().err.strip()


def test_08_note_already_sent(env, capsys):
    env["claude"] = (0, claude_stdout(result()))
    env["in_inbox_after"] = False
    assert env["run"]() == 0
    assert env["updated"] == []
    assert capsys.readouterr().err.strip()


def test_09_claude_runs_without_tools(env):
    env["claude"] = (0, claude_stdout(result()))
    env["run"]()
    args, kw = env["claude_calls"][0]
    assert args[0].endswith("claude") and "-p" in args
    i = args.index("--tools")
    assert args[i + 1] == ""
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in args
    assert "彰化[甘澍宮]大火" in kw["input"]
    assert kw["timeout"] == ds.TIMEOUT
