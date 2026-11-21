# 規格 T42：新聞摘要加入 garmin「新使用者」統計

對應：T42（joplin 端），接 garmin-gps-tracker T37（產生統計檔，PR nimotw/garmin-gps-tracker#173）。本 repo 負責讀檔並放進摘要。

## 背景

garmin（liveway）每天 05:30 產生 `new-users.json`，列出最近 7 天第一次出現的新裝置與新分享連結。使用者希望在每天 05:50 的新聞摘要裡看到「上一篇摘要之後」出現的新使用者。

摘要會送進 Instapaper，而且 joplin-proxy 的頁面不用登入就能看，所以**只放統計，不放任何名稱或 id**。

## 範圍

做：
- 讀取 `new-users.json`（version 1），篩出上一篇摘要之後的新項目，在摘要最前面加「新使用者」一段。
- 只有新使用者、沒有新聞時，照樣發布摘要。
- 為本 repo 建立測試基礎（pytest），並為這個功能寫測試。

不做：
- 逐項列出裝置或連結（名稱、id、hash、label、帳號都不放）。
- 改成週報時統計檔 7 天視窗不夠的問題：改週報時再請 garmin 把 `windowDays` 調大。
- conifer 帳號（`joplin-cli2`）的摘要。

## 輸入

- 檔案路徑由環境變數 `NEWS_NEW_USERS_FILE` 指定；**沒設定 = 功能關閉**，摘要行為與現在完全相同。
- 格式見 garmin repo `docs/specs/T37-new-users-stats.md`。本功能只用到：
  `version`、`generated`、`devices[].firstSeen`、`devices[].county`、`links[].firstSeen`、`links[].opens`、`links[].countries`。
- 「上一篇摘要的時間」＝ Joplin 裡最近一則標題以 `NEWS_TITLE_PREFIX` 開頭的筆記的建立時間（沿用現有找舊摘要的查詢）。

## 期望行為與驗收條件

篩選：
1. `firstSeen` 嚴格大於上一篇摘要時間的項目才算新；等於或更早的不算。
2. 找不到上一篇摘要時，檔案裡的項目全部算新。
3. 只在發布時讀檔；只累積（collect）的那幾次不讀。

輸出（放在摘要開頭那行「… 產生，共 N 則 …」之後、所有新聞分類之前）：

```
## 新使用者

- 新裝置 3 台：嘉義縣（2）、台灣以外
- 新連結 1 個：被開啟 122 次（JP、TW）
```

4. 新裝置那行：台數 = 新裝置數量。縣市依 `firstSeen` 先後去重列出，同縣市超過 1 台時在後面標台數，例如「嘉義縣（2）」。`county` 為 null 時寫「未知」，「台灣以外」照原字。
5. 新連結那行：個數 = 新連結數量；開啟次數 = 新連結 `opens` 加總；國家碼 = 所有新連結 `countries` 的聯集，排序後以「、」連接。聯集為空時省略括號整段。
6. 新裝置為 0 時不出現那一行；新連結為 0 時不出現那一行；兩者都是 0 時**整段（含標題）不出現**。
7. 輸出中**不得出現**任何 `user`、`device`、`hash`、`label` 的值。
8. 新聞為 0 則、但有新使用者時，仍建立摘要筆記，內容只有開頭那行和「新使用者」段；開頭那行的新聞則數寫 0。
9. 新聞和新使用者都沒有時，跟現在一樣不建立筆記。

錯誤與邊界情況（以下都是**這一段不放、摘要照常產生**，並在 stderr 印一行警告；功能關閉時不印）：
10. 檔案不存在。
11. 檔案不是合法 JSON，或缺少 `version` / `generated` / `devices` / `links`。
12. `version` 不是 1。
13. 檔案過期：`generated` 的日期（台北時間）不是今天。
14. 個別項目的 `firstSeen` 無法解析時，跳過該項目，其他照常。

## 部署

- CronJob `news-digest-cron` 以 hostPath **唯讀**掛載整個目錄 `/home/dany/projs/garmin-gps-tracker/owntracks/.usage-logs/` 到 `/usage`，並設定 `NEWS_NEW_USERS_FILE=/usage/new-users.json`。
- 掛整個目錄而不是單一檔案：garmin 是寫 `.tmp` 再 rename，單一檔案的 bind mount 在 rename 後會停在舊檔。
- 叢集只有一個節點（`yidan13`），hostPath 可用。

## 限制

- 隱私：見驗收條件 7。
- 掛載的目錄裡還有 garmin 的 `access.log`、`usage-report.*`，Pod 讀得到（唯讀）。程式只讀 `new-users.json`。
- 相容性：`NEWS_NEW_USERS_FILE` 沒設定時，輸出與目前版本完全相同。

## 測試

- 新增 pytest（dev dependency），測試放 `tests/`，執行方式寫進 README：`uv run pytest`。
- 測試不連網路、不連 Joplin：讀檔、篩選、產生段落的函式獨立出來，以 fixture 檔和固定時間測試。
- 上面驗收條件 1–14 各對應至少一個測試。

## 回滾

把 ConfigMap 裡的 `NEWS_NEW_USERS_FILE` 拿掉並 `kubectl apply`，即關閉功能，不用重 build。要完全回滾則把 image 換回 `1.3.1`。

## 未決問題

無。
