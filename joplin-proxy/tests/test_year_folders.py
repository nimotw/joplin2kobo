"""規格 docs/specs/T41-proxy-year-folders.md 的驗收條件測試（編號對應規格）。今年 = 2027。"""
import pytest

import main


def setup_note(joplin, title, folder_id="f1", note_id="n1"):
    joplin.notes[note_id] = folder_id
    joplin.folders[folder_id] = title
    return note_id


@pytest.fixture
def recent2(monkeypatch):
    monkeypatch.setattr(main, "ALLOWED_RECENT_YEARS", 2)


# 1, 2, 3, 4, 5, 6
@pytest.mark.parametrize("title, status", [
    ("2027", 200),
    ("2026", 200),
    ("2025", 403),
    ("2028", 403),
    (" 2027 ", 200),
    ("2027 備份", 403),
    ("kobo", 403),
    ("fail", 403),
])
def test_year_titles(joplin, client, recent2, title, status):
    note = setup_note(joplin, title)
    assert client.get(f"/n/{note}").status_code == status


# 7
def test_id_list_allows_without_folder_lookup(joplin, client, recent2, monkeypatch):
    monkeypatch.setattr(main, "ALLOWED_FOLDER_IDS", {"f1"})
    note = setup_note(joplin, "kobo")
    assert client.get(f"/n/{note}").status_code == 200
    assert joplin.folder_calls == []


# 8
def test_id_list_only_no_folder_lookup(joplin, client, monkeypatch):
    monkeypatch.setattr(main, "ALLOWED_FOLDER_IDS", {"other"})
    note = setup_note(joplin, "2027")
    assert client.get(f"/n/{note}").status_code == 403
    assert joplin.folder_calls == []


# 9
def test_no_config_allows_all(joplin, client):
    note = setup_note(joplin, "kobo")
    assert client.get(f"/n/{note}").status_code == 200
    assert joplin.folder_calls == []


# 10
@pytest.mark.parametrize("error", ["conn", 500])
def test_folder_lookup_failure_is_502_and_not_cached(joplin, client, recent2, error):
    note = setup_note(joplin, "2027")
    joplin.folder_error = error
    r = client.get(f"/n/{note}")
    assert r.status_code == 502
    assert r.json()["detail"] == "Bad Gateway: failed to contact Joplin API"
    joplin.folder_error = None
    assert client.get(f"/n/{note}").status_code == 200
    assert len(joplin.folder_calls) == 2


# 11
def test_folder_title_cached(joplin, client, recent2):
    note = setup_note(joplin, "2027")
    client.get(f"/n/{note}")
    client.get(f"/n/{note}")
    assert joplin.folder_calls == ["f1"]


# 12
def test_folder_cache_expires(joplin, client, recent2, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(main, "_clock", lambda: now[0])
    note = setup_note(joplin, "2027")
    client.get(f"/n/{note}")
    now[0] += 599
    client.get(f"/n/{note}")
    assert len(joplin.folder_calls) == 1
    now[0] += 2  # 距第一次 601 秒
    client.get(f"/n/{note}")
    assert len(joplin.folder_calls) == 2


# 13
def test_recent_years_one(joplin, client, monkeypatch):
    monkeypatch.setattr(main, "ALLOWED_RECENT_YEARS", 1)
    note = setup_note(joplin, "2026")
    assert client.get(f"/n/{note}").status_code == 403


# 14
@pytest.mark.parametrize("value", ["abc", "-1", "1.5"])
def test_parse_recent_years_invalid(value):
    with pytest.raises(ValueError, match="ALLOWED_RECENT_YEARS"):
        main.parse_recent_years(value)


@pytest.mark.parametrize("value, expected", [(None, 0), ("", 0), (" 2 ", 2), ("0", 0)])
def test_parse_recent_years_valid(value, expected):
    assert main.parse_recent_years(value) == expected
