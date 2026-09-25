"""檢查繁中譯文的技術正確性與用語／格式一致性。

輸出 reports/lint.md（依檢查項目彙整）與 reports/lint.csv（逐條明細）。

用法：
    uv run kc-tc lint
    uv run kc-tc lint -c admin-ui -l warn
    uv run kc-tc lint --check placeholder,quote
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict

from .common import CJK, DATA_DIR, REPORT_DIR, Pair, load_pairs

LEVELS = {"error": 3, "warn": 2, "info": 1}
C = f"[{CJK}]"

# 僅出現在簡體中文的常見字（皆非繁中通用字）
SIMPLIFIED = re.compile(
    "[这们为时会对发还没过开关页门问间题击设说请让认证号码删换应该书权织项签择输显错误"
    "动态据网络链导创务软视频质级异档响识凭账长简现实际边处则读写执选标销闭启个产业东车"
    "专样览详细汇总单帐户]"
)

PLACEHOLDER = re.compile(r"\{\{[^{}]+\}\}|\{\d+(?:,[^{}]*)?\}|%[sd]|\$\{[^{}]+\}")
TAG = re.compile(r"</?\s*([a-zA-Z][a-zA-Z0-9]*)[^>]*>")
LONE_QUOTE = re.compile(r"(?<!')'(?!')")


def placeholders(s: str) -> Counter:
    """MessageFormat 的 {0,choice,...} 內文需翻譯，只比對參數編號與格式類型。"""
    return Counter(re.sub(r"^\{(\d+),\s*(\w+).*\}$", r"{\1,\2}", m) for m in PLACEHOLDER.findall(s))


def is_theme(component: str) -> bool:
    """theme-* / keycloak-* 組件經由 Java MessageFormat 處理，單引號需寫成 ''。"""
    return not component.endswith("-ui")


def load_tw_terms():
    rules = []
    for line in (DATA_DIR / "tw_terms.tsv").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t") + ["", "", "", ""]
        skip_en = re.compile(cols[4], re.I) if cols[4] else None
        rules.append((re.compile(cols[0]), cols[1], cols[2] or "warn", cols[3], skip_en))
    return rules


def check(p: Pair, tw_rules) -> list[tuple[str, str, str]]:
    """回傳 [(level, check, message)]"""
    out = []
    en, zh = p.en, p.zh or ""

    # --- 佔位符
    pe, pz = placeholders(en), placeholders(zh)
    if set(pe) != set(pz):
        miss, extra = set(pe) - set(pz), set(pz) - set(pe)
        msg = []
        if miss:
            msg.append("缺少 " + " ".join(sorted(miss)))
        if extra:
            msg.append("多出 " + " ".join(sorted(extra)))
        out.append(("error", "placeholder", "；".join(msg)))

    # --- HTML 標籤
    te, tz = Counter(TAG.findall(en)), Counter(TAG.findall(zh))
    if te != tz:
        out.append(("warn", "html", f"標籤不一致：原文 {dict(te)}，譯文 {dict(tz)}"))

    # --- 單引號（MessageFormat）
    if is_theme(p.component):
        if LONE_QUOTE.search(zh):
            out.append(("error", "quote", "MessageFormat 中單引號須寫成 ''，否則其後文字與佔位符會失效"))
    elif "''" in zh and "''" not in en:
        out.append(("warn", "quote", "非 MessageFormat 組件，'' 會原樣顯示為兩個單引號"))

    # --- 簡體字
    simp = set(SIMPLIFIED.findall(zh))
    if simp:
        out.append(("error", "simplified", "疑似簡體字：" + "".join(sorted(simp))))

    # --- 台灣用語
    for rx, sugg, level, note, skip_en in tw_rules:
        if skip_en and skip_en.search(en):
            continue
        for m in set(rx.findall(zh)):
            out.append((level, "tw-term", f"「{m}」→「{sugg}」" + (f"（{note}）" if note else "")))

    # --- 人稱
    if "你" in zh:
        out.append(("warn", "pronoun", "專案慣例使用「您」"))

    # --- 標點
    zh_np = PLACEHOLDER.sub("", zh)
    if m := re.findall(C + r"([,;:!?])(?=\s|$|" + C + ")", zh_np):
        out.append(("warn", "punct", "中文後使用半形標點：" + " ".join(sorted(set(m)))))
    if re.search(C + r"\.$", zh_np):
        out.append(("warn", "punct", "中文句尾使用半形句點，應為「。」"))
    # 「(」「)」是引用半形括號本身，不算
    if re.search(r"(?<!「)\((?!」)[^()]*" + C + r"[^()]*(?<!「)\)(?!」)", zh_np):
        out.append(("info", "punct", "括號內含中文，建議使用全形括號（）"))
    if re.search(r"[，。：；！？][ \t]", zh):   # 換行不算；用原文以免佔位符去掉後留下空白
        out.append(("info", "punct", "全形標點後多了空白"))

    # --- 中英文間距（專案慣例：中文與英數之間加半形空白）
    if re.search(C + r"[A-Za-z0-9]|[A-Za-z0-9]" + C, zh_np):
        out.append(("warn", "spacing", "中文與英數字之間缺少空白"))
    if re.search(r"\s{2,}", zh.strip()) and not re.search(r"\s{2,}", en.strip()):
        out.append(("info", "spacing", "連續多個空白"))

    # --- 前後空白
    if (en[:1].isspace(), en[-1:].isspace()) != (zh[:1].isspace(), zh[-1:].isspace()):
        out.append(("info", "whitespace", "開頭／結尾空白與原文不一致"))

    # --- 疑似未翻譯
    if zh == en and re.search(r"[a-z]{3,}\s+[a-z]{3,}", en):
        out.append(("info", "untranslated", "譯文與原文相同"))

    out += [("warn", "weblate", msg) for msg in weblate_checks(en, zh)]
    return out


# Weblate 品質檢查（weblate/checks/chars.py）中與繁中相關的句尾標點與換行檢查，
# 讓上傳前就能發現 Weblate 會標記的問題。中文的「。」「：」可對應英文句點。
END_MARKS = [
    # (check_id, 原文結尾, 譯文可接受的結尾, 說明)
    ("end_ellipsis", ("...", "…"), ("...", "…"), "刪節號"),
    ("end_stop", (".",), (".", "。"), "句號"),
    ("end_colon", (":",), (":", "："), "冒號"),
    ("end_question", ("?",), ("?", "？"), "問號"),
    ("end_exclamation", ("!",), ("!", "！"), "驚嘆號"),
]


def weblate_checks(en: str, zh: str) -> list[str]:
    out = []
    s, t = en.rstrip(), zh.rstrip()
    if s and t:
        src_end = next((cid for cid, src, _, _ in END_MARKS if s.endswith(src)), None)
        tgt_end = next((cid for cid, _, tgt, _ in END_MARKS if t.endswith(tgt)), None)
        # 原文以句點結尾時，譯文用「：」也可接受（Weblate 對中日韓語言的規則）
        if src_end == "end_stop" and t.endswith("："):
            tgt_end = src_end
        if src_end != tgt_end:
            cid, name = next((cid, name) for cid, _, _, name in END_MARKS if cid in (src_end or tgt_end))
            if src_end:
                out.append(f"{cid}：原文以{name}結尾，譯文沒有")
            else:
                out.append(f"{cid}：譯文以{name}結尾，原文沒有")
    if en.count("\n") != zh.count("\n"):
        out.append(f"newline_count：換行數不一致（原文 {en.count(chr(10))}，譯文 {zh.count(chr(10))}）")
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog=f"kc-tc {__name__.rsplit('.', 1)[-1]}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--component", action="append", help="只檢查指定組件（可重複）")
    ap.add_argument("-l", "--level", default="info", choices=LEVELS, help="最低顯示層級")
    ap.add_argument("--check", help="只執行指定檢查，逗號分隔")
    args = ap.parse_args(argv)
    only_checks = set(args.check.split(",")) if args.check else None

    tw_rules = load_tw_terms()
    pairs = load_pairs(args.component)
    issues = []
    missing = Counter()
    for p in pairs:
        if p.zh is None:
            missing[p.component] += 1
            continue
        for level, name, msg in check(p, tw_rules):
            if LEVELS[level] < LEVELS[args.level] or (only_checks and name not in only_checks):
                continue
            issues.append((p, level, name, msg))

    REPORT_DIR.mkdir(exist_ok=True)
    with (REPORT_DIR / "lint.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["level", "check", "component", "key", "line", "message", "en", "zh_Hant"])
        for p, level, name, msg in issues:
            w.writerow([level, name, p.component, p.key, p.line, msg, p.en, p.zh])

    by_check = defaultdict(list)
    for it in issues:
        by_check[(it[1], it[2])].append(it)

    md = ["# Lint 報告", "", "| 層級 | 檢查 | 數量 |", "|---|---|---:|"]
    order = sorted(by_check, key=lambda k: (-LEVELS[k[0]], k[1]))
    md += [f"| {lv} | {ck} | {len(by_check[(lv, ck)])} |" for lv, ck in order]
    if missing:
        md += ["", "## 未翻譯字串", ""] + [f"- {c}: {n}" for c, n in sorted(missing.items())]
    for lv, ck in order:
        md += ["", f"## [{lv}] {ck}", ""]
        for p, _, _, msg in by_check[(lv, ck)]:
            md.append(f"- `{p.component}:{p.line}` **{p.key}** — {msg}")
            md.append(f"  - en: {md_escape(p.en)}")
            md.append(f"  - zh: {md_escape(p.zh)}")
    (REPORT_DIR / "lint.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"共 {len(issues)} 項問題：")
    for lv, ck in order:
        print(f"  [{lv:5}] {ck:12} {len(by_check[(lv, ck)])}")
    if missing:
        print("未翻譯：" + "，".join(f"{c} {n}" for c, n in sorted(missing.items())))
    print("詳見 reports/lint.md、reports/lint.csv")


def md_escape(s: str | None) -> str:
    s = (s or "").replace("\n", "⏎")
    return s if len(s) <= 300 else s[:300] + "…"


if __name__ == "__main__":
    main()
