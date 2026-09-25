# Keycloak 繁體中文（zh_Hant）翻譯工作區

從 [Hosted Weblate](https://hosted.weblate.org/projects/keycloak/) 下載 Keycloak 各組件的繁中翻譯，在本地統一詞彙、優化文字。
使用 [uv](https://docs.astral.sh/uv/) 管理（Python 3.14）；網頁介面使用 FastAPI、Jinja2、htmx。

## 目錄結構

```
glossary.csv              ← 本地標準詞彙表（第一次下載時用 Weblate 詞彙表初始化，之後自行維護）
data/tw_terms.tsv         ← 中國大陸用語 → 台灣用語的檢查規則
glossary/weblate.{tbx,csv} ← Weblate 上的詞彙表（每次 pull 都會覆蓋，僅供參考）
translations/<組件>/
    en.properties         ← 英文原文
    zh_Hant.properties    ← 繁中譯文（直接修改這個檔案）
translations/manifest.json ← 各組件的翻譯進度與下載時間
baseline/<組件>.json      ← 上次與 Weblate 同步時的譯文（判斷本地修改與衝突用，pull/push 自動維護）
reports/                  ← 產生的報告
src/kc_tc/                ← 工具原始碼（kc-tc 指令）
```

## 使用流程

```bash
# 1. 下載（匿名每天只有 100 次 API 額度，建議設定 API key）
uv run kc-tc pull                      # 全部組件；本地未上傳的修改會保留
uv run kc-tc pull admin-ui             # 單一組件
git add -A && git commit -m "chore(pull): 同步 Weblate"

# 2. 詞彙一致性
uv run kc-tc terms                     # 依 glossary.csv 產生 reports/terms.md
uv run kc-tc terms --term session      # 查某個英文詞目前有哪些譯法

# 3. 網頁介面：詞彙決策、批次取代、未翻譯、需要編輯、上傳（http://127.0.0.1:8765）
uv run kc-tc serve                     # 加 --host 0.0.0.0 開放給區網（無登入驗證，請注意）

# 4. 格式與用語檢查
uv run kc-tc lint                      # 產生 reports/lint.md
uv run kc-tc lint -l warn -c admin-ui
uv run kc-tc lint --check placeholder,quote

# 5. 上傳到 Weblate（需要 API key，也可在網頁的「上傳」頁操作）
uv run kc-tc push --dry-run            # 比對本地修改與 Weblate 現值
uv run kc-tc push                      # 上傳（詢問確認）
git add baseline && git commit -m "chore(push): 上傳譯文到 Weblate"
```

API key 在 Weblate 個人設定的「API access」取得，寫進專案根目錄的 `.env`（見 `.env.example`，已在 .gitignore）。
Weblate API 限制：匿名每天 100 次、登入後每小時 5000 次。

## 網頁介面（`kc-tc serve`）

- **詞彙決策**：左側依「不一致條數」排序列出詞彙；點進去後，含有該詞的字串會依目前譯法分組（標準／變體／自動候選）。
  在分組上點「設為標準」或「設為變體」，再按「儲存詞彙表」寫回 `glossary.csv`。左上角可輸入新的英文詞彙來探索譯法。
- **批次取代**：依詞彙表把變體取代為標準譯法，逐條顯示修改前後的差異，可勾選、手動改寫後寫入 `zh_Hant.properties`。
  寫入時只替換該行，並檢查檔案是否已被其他地方修改；寫入後會即時顯示 lint 結果。
- **未翻譯**：依組件與關鍵字列出尚未翻譯的字串，原文中的詞彙表術語會標出標準譯法。輸入時自動以 lint 檢查，
  Ctrl+Enter 儲存；有 error 等級問題（例如缺少佔位符）時須勾選「仍要儲存」。新 key 依 `en.properties` 的順序插入，
  儲存後出現在「上傳」頁的本地修改中。
- **需要編輯**：Weblate 上標為「需要編輯／需要重寫／需要檢查」的字串（通常是英文原文改過），並顯示英文舊原文與新原文的差異。
  清單在 `kc-tc pull` 時記錄於 `baseline/needs_edit.json`；編輯方式與「未翻譯」相同，
  上傳時檢閱狀態選「等候檢閱」或「已核可」即會從清單移除。

- **上傳**：列出本地與 `baseline/` 不同的字串，按「比對 Weblate」查詢現值後分成「可上傳」與「衝突」
  （下載後 Weblate 上有人改過同一條，預設不勾選）。勾選後在背景逐條上傳並即時顯示進度；
  上傳前會再確認 Weblate 現值沒有變動。上傳後的檢閱狀態可選「需要編輯」、「等候檢閱」（預設）或「已核可」（需要檢閱權限），與 Weblate 介面一致。
  「本地預覽」與比對結果的每條都有「退回」按鈕：把本地譯文還原為 `baseline/` 的值（新翻譯則刪除），從待上傳清單移除。
  比對結果上方的「Weblate 目前狀態」標籤（未翻譯、已核可…）可點選，只勾選該狀態的項目。

網頁直接讀寫 repo 內的檔案，修改都能用 `git diff` 檢視、用 `git checkout` 還原。
在遠端機器上執行時，可用 `ssh -L 8765:127.0.0.1:8765 <主機>` 轉發後在本機瀏覽器開啟。

## glossary.csv

| 欄位 | 說明 |
|---|---|
| en | 英文詞彙，比對時不分大小寫、容許複數；較長的詞優先（檢查 client 時會略過 client scope） |
| zh_Hant | 標準譯法，多個可接受譯法用 `\|` 分隔 |
| variants | 已知的非標準譯法，用 `\|` 分隔，報告會分開統計 |
| note | 備註 |
| reviewed | 已檢視標記，存標記當下的不一致條數（由網頁維護）。之後條數增加會移回待處理並顯示「有新變動」 |

## lint 檢查項目

| 檢查 | 說明 |
|---|---|
| placeholder | `{0}`、`{{name}}`、`${vault.ID}` 等佔位符與原文不一致 |
| quote | theme-* 組件經 Java MessageFormat 處理，`'` 必須寫成 `''`；*-ui 組件則相反 |
| html | HTML 標籤與原文不一致 |
| simplified | 殘留簡體字 |
| tw-term | 中國大陸用語（規則在 `data/tw_terms.tsv`） |
| pronoun | 使用「你」（專案慣例為「您」） |
| punct | 中文後使用半形標點、半形括號包住中文等 |
| spacing | 中文與英數字之間缺少空白（專案慣例是要加空白） |
| whitespace / untranslated | 前後空白不一致、疑似未翻譯 |
| weblate | 與 Weblate 品質檢查相同的句尾標點（句號、冒號、問號、驚嘆號、刪節號）與換行數檢查；中文的「。」「：」可對應英文句點 |

## 注意事項

- 直接修改 `zh_Hant.properties` 時，保留原本的 key 與跳脫格式（例如行尾 `\` 表示續行）。
- pull 採三方合併：Weblate 更新、本地沒改的直接套用；本地改過、Weblate 沒動的保留本地值；
  雙方都改的保留本地值並在上傳時標為衝突。
- 網頁沒有登入驗證，但會擋掉跨站請求（POST 必須由頁面上的 htmx 送出）。開放 0.0.0.0 時，
  同網段的人都能用你的 API key 上傳，請只在可信任的網路使用。

## 開發

```bash
pre-commit install    # 啟用 commit-msg（Conventional Commits）與基本檢查
```

Commit 規範見 CLAUDE.md。
