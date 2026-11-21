"""joplin-proxy 測試共用設定：不連真的 Joplin，requests.get 以假的 API 取代。"""
import os

import pytest
import requests

# main.py 在 import 時讀環境變數，要在 import 前設好
os.environ.setdefault("JOPLIN_DATA_API_URL", "http://joplin.test")
os.environ.setdefault("JOPLIN_DATA_API_TOKEN", "test-token")
os.environ.pop("ALLOWED_FOLDER_IDS", None)
os.environ.pop("ALLOWED_RECENT_YEARS", None)

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class FakeJoplin:
    """假的 Joplin Data API：notes[id] = parent_id，folders[id] = title。"""

    def __init__(self):
        self.notes = {}
        self.folders = {}
        self.folder_calls = []
        self.folder_error = None  # "conn" 或 HTTP 狀態碼

    def get(self, url, **kwargs):
        path = url.replace("http://joplin.test", "")
        if path.startswith("/notes/"):
            note_id = path.split("/")[2]
            if note_id not in self.notes:
                return FakeResponse(404)
            return FakeResponse(200, {"id": note_id, "parent_id": self.notes[note_id],
                                      "title": "t", "body": "hello"})
        if path.startswith("/folders/"):
            folder_id = path.split("/")[2]
            self.folder_calls.append(folder_id)
            if self.folder_error == "conn":
                raise requests.ConnectionError("boom")
            if self.folder_error:
                return FakeResponse(self.folder_error)
            if folder_id not in self.folders:
                return FakeResponse(404)
            return FakeResponse(200, {"id": folder_id, "title": self.folders[folder_id]})
        raise AssertionError(f"unexpected GET {url}")


@pytest.fixture
def joplin(monkeypatch):
    fake = FakeJoplin()
    monkeypatch.setattr(main.requests, "get", fake.get)
    monkeypatch.setattr(main, "ALLOWED_FOLDER_IDS", None)
    monkeypatch.setattr(main, "ALLOWED_RECENT_YEARS", 0)
    monkeypatch.setattr(main, "_current_year", lambda: 2027)
    main._folder_title_cache.clear()
    return fake


@pytest.fixture
def client():
    return TestClient(main.app)
