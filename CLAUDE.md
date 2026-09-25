# Keycloak 繁體中文翻譯工作區

從 Hosted Weblate 下載 Keycloak 各組件的 zh_Hant 翻譯，在本地統一詞彙與優化文字。工具說明見 README.md。

## Commit 規範

使用 [Conventional Commits](https://www.conventionalcommits.org/)：`<type>(<scope>): <描述>`，描述可用中文。

| type | 用途 |
|---|---|
| feat / fix / refactor | `src/kc_tc/` 工具程式的功能、修正、重構 |
| i18n | 修改 `translations/*/zh_Hant.properties` 譯文 |
| chore | 從 Weblate 下載（`chore(pull): ...`）、上傳後更新基準（`chore(push): ...`）、相依套件、設定 |
| docs | README、CLAUDE.md 等文件 |
| ci | pre-commit、GitHub Actions |

常用 scope：`pull`、`terms`、`lint`、`glossary`、組件名稱（如 `i18n(admin-ui): 統一 client 譯法`）。

- 從 Weblate 下載的更新與本地譯文修改要分開 commit，方便日後比對與上傳。
- `baseline/` 記錄上次與 Weblate 同步時的譯文，只由 pull/push 更新，不要手動修改。
- commit-msg 格式由 pre-commit 檢查（`.pre-commit-config.yaml`）。

## pre-commit / CI

- 有需要時才加入 pre-commit hook 或 CI；新增前先說明理由。
- hook 不可改動 `translations/`、`glossary/` 內從 Weblate 下載的檔案（例如自動去除行尾空白），以免與 Weblate 產生無意義的差異。

## 詞彙表

- 以本地 `glossary.csv` 為唯一標準；`glossary/weblate.*` 只是 Weblate 詞彙表的參考副本。
- 不把詞彙表同步回 Weblate：Weblate 的 glossary 組件會把詞條同步到所有語言，維護者認為不好用。
- 詞彙表只放術語與不翻譯的專有名詞，不放完整句子或代名詞（「您／你」由 lint 的 pronoun 檢查處理）。

## 翻譯慣例（lint 規則依據）

- 中文與英數字之間加半形空白；使用全形標點；人稱用「您」。
- theme-* / keycloak-* 組件經 Java MessageFormat 處理，單引號寫成 `''`；*-ui 組件用單一 `'`。
