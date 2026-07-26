# Maple Classic Discord Center

定時讀取《新楓之谷：經典版》官方公告 JSON API，發現新公告時以 Discord Embed 推播。V1 不使用 AI 摘要、資料庫、Bot Token 或 Slash Command。

## 資料來源

實際檢查官網前端程式後，公告來自：

```text
POST https://maplestoryclassic.beanfun.com/api/Bulletin/FindBulletin
Content-Type: application/json

{"pageSize":50,"kind":0,"page":1,"method":6}
```

程式直接使用這個 JSON API，沒有猜測或依賴 HTML selector。官網目前的分類 ID 為 760（活動）、759（更新）、758（重要）；其他分類顯示為「綜合」。

## Windows 本機安裝

先安裝 Python 3.11 以上版本，然後在 PowerShell 進入專案目錄。

建立並啟用虛擬環境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

若 PowerShell 阻擋啟用腳本，可只在目前視窗調整：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

安裝 dependencies：

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 設定環境變數

複製範例檔並填入自己的 Webhook：

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env`：

```dotenv
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/你的資料
TEST_MODE=false
STATE_FILE=data/state.json
REQUEST_TIMEOUT=15
MAPLE_THUMBNAIL_URL=
```

`MAPLE_THUMBNAIL_URL` 為可選設定，必須是 HTTPS 圖片網址；未設定時 Embed 不顯示縮圖。建議使用自行控制或穩定託管的圖片網址。`.env` 與執行時狀態檔都已被 `.gitignore` 排除，請勿提交 `.env`、真正的 Webhook URL 或其他 Secret。

## Discord Embed 顯示

每則公告 Embed 會顯示分類、日期、公告編號與官方公告連結；分類保留既有的顏色與 Icon，標題也保留分類 Icon。設定 `MAPLE_THUMBNAIL_URL` 時，該圖片會同時顯示為 author、縮圖與 footer 圖示。

## 測試 Discord Webhook

將 `.env` 的 `TEST_MODE` 暫時改成 `true`：

```powershell
python app.py
```

TEST_MODE 每次都會發送官網目前最新一篇公告，方便重複驗證 Webhook。若這是第一次執行，成功後也會建立完整公告基準；若發送失敗，不會建立或改寫狀態。

測試後務必改回：

```dotenv
TEST_MODE=false
```

## 正常執行

```powershell
python app.py
```

第一次正常執行只會把目前所有公告寫入 `data/state.json` 作為基準，不會洗歷史公告。往後只推播新增公告。每篇公告只有在 Discord 成功接收後才會原子更新狀態檔；失敗公告不會被標記為已發送。

執行測試：

```powershell
python -m pytest
```

## GitHub Actions

Workflow 每 15 分鐘執行一次，也支援手動執行。狀態檔透過 GitHub Actions Cache 跨執行保存，不會 commit 回 repository。

### 設定 GitHub Secret

1. 在 GitHub repository 開啟 **Settings**。
2. 前往 **Secrets and variables → Actions**。
3. 點選 **New repository secret**。
4. 名稱輸入 `DISCORD_WEBHOOK_URL`。
5. 值貼上 Discord Webhook URL 並儲存。

Workflow 只從 `${{ secrets.DISCORD_WEBHOOK_URL }}` 讀取 Webhook。

### 手動測試 GitHub Actions

1. 前往 repository 的 **Actions**。
2. 選擇 **Check Maple Classic announcements**。
3. 點選 **Run workflow**。
4. 首次建議勾選 `test_mode`，會送出最新一篇測試公告。
5. 確認成功後，再以未勾選的正常模式執行一次。

注意：GitHub 排程使用 UTC 表示，但 `*/15 * * * *` 在所有時區都代表每 15 分鐘一次。GitHub 排程可能因平台負載稍有延遲。

## 常見錯誤排除

- `缺少 DISCORD_WEBHOOK_URL`：確認 `.env` 名稱正確、沒有存成 `.env.txt`；GitHub 上確認 Secret 名稱完全一致。
- Discord 回傳 `401` 或 `404`：Webhook URL 無效、已刪除或複製不完整，請在 Discord 重新建立。
- Discord 回傳 `429`：遇到頻率限制；本次會失敗且不標記公告，下次排程會重試。
- `官網 API 回應格式與預期不符`：官網可能改版。程式會停止且不改狀態，請重新檢查官網 API。
- `狀態檔 ... 格式不正確`：不要直接覆蓋損壞檔案。先備份並檢查 JSON；刪除狀態檔會讓下一次正常執行重新建立基準。
- GitHub 每次都顯示首次執行：到 Actions 執行紀錄確認 **Restore announcement state** 與 **Save announcement state** 步驟成功，並確認 repository 沒有停用 Actions Cache。
- Windows SSL 或連線錯誤：確認系統時間、網路、防毒 HTTPS 攔截與公司 Proxy 設定；也可提高 `REQUEST_TIMEOUT`。

## 安全與限制

- 不要把 `.env` 或 Webhook 貼進 issue、log、commit。
- V1 僅同步新公告；不包含公告內容摘要或 Discord 指令。
- 程式設定合理 timeout 與專用 User-Agent，官網或 Discord 失敗時以非零狀態結束。
