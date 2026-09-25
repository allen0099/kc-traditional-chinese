# Keycloak 繁體中文（zh_Hant）翻譯工作區

從 [Hosted Weblate](https://hosted.weblate.org/projects/keycloak/) 下載 Keycloak 各組件的繁中翻譯，在本地統一詞彙、優化文字。
使用 [uv](https://docs.astral.sh/uv/) 管理（Python 3.14），沒有第三方相依套件。

## 目錄結構

```
glossary.csv              ← 本地標準詞彙表（第一次下載時用 Weblate 詞彙表初始化，之後自行維護）
data/tw_terms.tsv         ← 中國大陸用語 → 台灣用語的檢查規則
glossary/weblate.{tbx,csv} ← Weblate 上的詞彙表（每次 pull 都會覆蓋，僅供參考）
translations/<組件>/
    en.properties         ← 英文原文
    zh_Hant.properties    ← 繁中譯文（直接修改這個檔案）
translations/manifest.json ← 各組件的翻譯進度與下載時間
reports/                  ← 產生的報告
src/kc_tc/                ← 工具原始碼（kc-tc 指令）
```

## 使用流程

```bash
# 1. 下載（不需要 API key）
uv run kc-tc pull                      # 全部組件
uv run kc-tc pull admin-ui             # 單一組件
git add -A && git commit -m "pull"     # 每次下載後 commit，之後用 git diff 追蹤修改

# 2. 詞彙一致性
uv run kc-tc terms                     # 依 glossary.csv 產生 reports/terms.md
uv run kc-tc terms --term session      # 查某個英文詞目前有哪些譯法

# 3. 格式與用語檢查
uv run kc-tc lint                      # 產生 reports/lint.md
uv run kc-tc lint -l warn -c admin-ui
uv run kc-tc lint --check placeholder,quote
```

## glossary.csv

| 欄位 | 說明 |
|---|---|
| en | 英文詞彙，比對時不分大小寫、容許複數；較長的詞優先（檢查 client 時會略過 client scope） |
| zh_Hant | 標準譯法，多個可接受譯法用 `\|` 分隔 |
| variants | 已知的非標準譯法，用 `\|` 分隔，報告會分開統計 |
| note | 備註 |

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

## 注意事項

- 直接修改 `zh_Hant.properties` 時，保留原本的 key 與跳脫格式（例如行尾 `\` 表示續行）。
- 上傳回 Weblate（`kc-tc push`）尚未實作，需要 API key。上傳前應重新 pull 並比對，避免覆蓋別人在這段期間的修改。

## 開發

```bash
pre-commit install    # 啟用 commit-msg（Conventional Commits）與基本檢查
```

Commit 規範見 CLAUDE.md。
