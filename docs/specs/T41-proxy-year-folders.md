# 規格 T41：joplin-proxy 依筆記本名稱（年份）開放

## 背景

joplin-proxy 用 `ALLOWED_FOLDER_IDS` 寫死可讀的筆記本 id。note2read 每年用筆記本名稱（例如 `2027`）找當年的筆記本，找不到才會建立，所以 id 要等 1/1 之後才知道。在設定還沒改之前，新筆記會被移到新年份的筆記本，proxy 就會回 403，Instapaper 抓到的是錯誤頁。

另外，conifer 帳號有兩個名叫 `2026` 的筆記本。設定開放的 `6d9159be…` 是空的，筆記其實都在 `7c20af3d…`，所以 conifer 的 proxy 現在對每一篇都回 403。

## 範圍

做：
- 新增設定 `ALLOWED_RECENT_YEARS`（整數 N）：筆記所在筆記本的名稱是最近 N 年的年份（今年、去年……），就允許讀取。
- 跟 `ALLOWED_FOLDER_IDS` 取聯集：符合任一條件就允許。
- nimo、conifer 兩個部署都設定 `ALLOWED_RECENT_YEARS=2`（今年＋去年）。

不做：
- 不改 note2read 建立或選擇年份筆記本的方式。
- 不合併或刪除 conifer 重複的 `2026` 筆記本。
- 不改 `/r/{resource_id}` 圖片路由的權限（現在本來就不檢查筆記本）。

## 期望行為

「今年」用容器的本地時間判斷（目前兩個容器都是 UTC，跟 note2read 一樣）。

1. 兩個設定都沒有，或都是空的：不檢查筆記本，跟現在一樣。
2. 只設 `ALLOWED_FOLDER_IDS`：跟現在一樣，不會多查筆記本名稱。
3. 有設 `ALLOWED_RECENT_YEARS=N`（N ≥ 1）：
   - 筆記的 `parent_id` 在 `ALLOWED_FOLDER_IDS` 裡 → 允許，不查筆記本名稱。
   - 否則查 `GET /folders/{parent_id}?fields=id,title`：名稱去掉前後空白後，正好是 `今年 - N + 1` 到 `今年` 之間的四位數年份 → 允許；其他情況回 403（訊息不變）。
4. 查筆記本名稱失敗（連線錯誤、非 200）→ 502，`Bad Gateway: failed to contact Joplin API`；不快取失敗的結果。
5. 筆記本名稱快取在記憶體 600 秒（以 folder id 為 key），同一個筆記本 10 分鐘內只查一次。
6. `ALLOWED_RECENT_YEARS` 不是整數，或小於 0 → 啟動時直接失敗（丟 `ValueError`），訊息帶設定名稱；`0` 等於沒設。

## 驗收條件（→ 測試，`joplin-proxy/tests/`，用 pytest＋FastAPI TestClient，Joplin API 以 mock 取代）

設今年 = 2027、N = 2：

| # | 情境 | 結果 |
|---|---|---|
| 1 | 筆記在名稱 `2027` 的筆記本 | 200 |
| 2 | 名稱 `2026` | 200 |
| 3 | 名稱 `2025` | 403 |
| 4 | 名稱 `2028`（未來年份） | 403 |
| 5 | 名稱 ` 2027 `（前後有空白） | 200 |
| 6 | 名稱 `2027 備份`、`kobo`、`fail` | 403 |
| 7 | parent_id 在 `ALLOWED_FOLDER_IDS`，名稱是 `kobo` | 200，且沒有呼叫 `/folders` |
| 8 | 只設 `ALLOWED_FOLDER_IDS`，筆記在 `2027` 但 id 不在清單 | 403，且沒有呼叫 `/folders` |
| 9 | 兩個設定都沒設 | 200，且沒有呼叫 `/folders` |
| 10 | `/folders` 連線錯誤或回 500 | 502；下一次請求會重新查 |
| 11 | 同一個筆記本連續請求兩次 | `/folders` 只呼叫一次 |
| 12 | 快取超過 600 秒 | 重新查 |
| 13 | N = 1，名稱 `2026` | 403 |
| 14 | `ALLOWED_RECENT_YEARS=abc` 或 `-1` | 解析設定時丟 `ValueError` |

部署後手動確認（記在 PR）：nimo 一篇 `2026` 的筆記 200、一篇 `kobo` 的筆記 403；conifer `7c20af3d` 裡的筆記從 403 變 200。

## 限制

- joplin-proxy 目前沒有測試，要先建立測試基礎：pytest 放在 dev dependency、`joplin-proxy/tests/`，怎麼跑寫進 README。
- 設定改在 `IaC/joplin2readeck/joplin-proxy/config.yaml`（nimo）、`joplin-proxy-cli2.yaml`（conifer），以及 repo 內的 `joplin-proxy/config.yaml` 範本。`IaC/` 不在 git 裡，PR 會附上改動內容。

## 未決問題

- conifer 空的 `2026`（`6d9159be…`）要不要刪？不在本項處理，要的話另開一項。
