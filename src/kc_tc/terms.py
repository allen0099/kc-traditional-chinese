"""詞彙一致性報告：依 glossary.csv 檢查每個英文詞彙在譯文中的譯法是否統一。

glossary.csv 欄位：
    en        英文詞彙（比對時不分大小寫、容許複數）
    zh_Hant   標準譯法，多個可接受譯法以 | 分隔
    variants  已知的非標準譯法，以 | 分隔（報告中會分別統計）
    note      備註

用法：
    uv run kc-tc terms                      # 產生 reports/terms.md、reports/terms.csv
    uv run kc-tc terms --term realm         # 探索某詞彙目前有哪些譯法（不需在詞彙表中）
    uv run kc-tc terms --term "client" -n 30   # 顯示更多例句
"""
from __future__ import annotations

import argparse
import csv
import math
import re
from collections import Counter

from .common import CJK, REPORT_DIR, Pair, load_glossary, load_pairs, split_alts


def term_regex(term: str) -> re.Pattern:
    words = [re.escape(w) for w in term.split()]
    body = r"[\s-]+".join(words)
    return re.compile(rf"(?<![\w-]){body}(?:s|es)?(?![\w-])", re.I)


def ngrams(s: str, lo: int = 2, hi: int = 4) -> set[str]:
    out = set()
    for run in re.findall(f"[{CJK}]+", s):
        for n in range(lo, hi + 1):
            out.update(run[i:i + n] for i in range(len(run) - n + 1))
    return out


def candidates(matched: list[Pair], all_pairs: list[Pair], top: int = 10) -> list[tuple[str, int, float]]:
    """找出在含有該英文詞的譯文中特別常見的中文片段（候選譯法）。"""
    df_all = Counter()
    for p in all_pairs:
        df_all.update(ngrams(p.zh))
    df = Counter()
    for p in matched:
        df.update(ngrams(p.zh))
    n, total = len(matched), len(all_pairs)
    scored = []
    for g, c in df.items():
        if c < 2 and n > 3:
            continue
        cov = c / n
        scored.append((g, c, cov * math.log(total / df_all[g]) * (1 + 0.15 * len(g))))
    scored.sort(key=lambda x: -x[2])
    # 去掉被更長且同樣頻率的片段涵蓋者
    picked: list[tuple[str, int, float]] = []
    for g, c, s in scored:
        if any(g in pg and c <= pc for pg, pc, _ in picked):
            continue
        picked = [x for x in picked if not (x[0] in g and x[1] <= c)]
        picked.append((g, c, s))
        if len(picked) >= top:
            break
    return picked


def explore(term: str, pairs: list[Pair], limit: int) -> None:
    rx = term_regex(term)
    matched = [p for p in pairs if rx.search(p.en)]
    print(f"「{term}」出現在 {len(matched)} 條已翻譯字串中\n")
    if not matched:
        return
    print("候選譯法（出現次數／覆蓋率）：")
    for g, c, _ in candidates(matched, pairs, 15):
        print(f"  {g:10}\t{c:4}\t{c / len(matched):5.0%}")
    print(f"\n例句（前 {limit} 條）：")
    for p in matched[:limit]:
        print(f"  [{p.component}] {p.key}\n    en: {p.en[:160]}\n    zh: {p.zh[:160]}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog=f"kc-tc {__name__.rsplit('.', 1)[-1]}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--term", help="探索單一英文詞彙目前的譯法")
    ap.add_argument("-n", type=int, default=15, help="--term 模式顯示的例句數")
    ap.add_argument("-c", "--component", action="append", help="只分析指定組件（可重複）")
    args = ap.parse_args(argv)

    pairs = [p for p in load_pairs(args.component) if p.zh]
    if args.term:
        explore(args.term, pairs, args.n)
        return

    # 合併大小寫／單複數相同的詞彙
    glossary: dict[str, dict] = {}
    for r in load_glossary():
        k = re.sub(r"(es|s)$", "", r["en"].strip().lower())
        g = glossary.setdefault(k, {"en": r["en"].strip(), "ok": [], "var": [], "note": r.get("note", "")})
        g["ok"] += [x for x in split_alts(r["zh_Hant"]) if x not in g["ok"]]
        g["var"] += [x for x in split_alts(r.get("variants")) if x not in g["var"]]

    # 較長的詞彙優先：檢查 "client" 時先遮蔽 "client scope"
    longer = {k: [term_regex(o["en"]) for o in glossary.values()
                  if o["en"].lower() != g["en"].lower() and term_regex(g["en"]).search(o["en"])]
              for k, g in glossary.items()}

    rows, summary = [], []
    for k, g in sorted(glossary.items(), key=lambda kv: kv[1]["en"].lower()):
        rx = term_regex(g["en"])
        matched = []
        for p in pairs:
            en = p.en
            for lrx in longer[k]:
                en = lrx.sub(" ", en)
            if rx.search(en):
                matched.append(p)
        stat = Counter()
        bad = []
        for p in matched:
            if any(x in p.zh for x in g["ok"]):
                stat["ok"] += 1
                continue
            hit = [v for v in g["var"] if v in p.zh]
            stat["variant" if hit else "other"] += 1
            bad.append(p)
            rows.append([g["en"], "|".join(g["ok"]), "variant" if hit else "other", "|".join(hit),
                         p.component, p.key, p.line, p.en, p.zh])
        n = len(matched)
        cands = [f"{c}({n_})" for c, n_, _ in candidates(bad, pairs, 5)] if len(bad) >= 2 else []
        summary.append((g, n, stat, cands, bad))

    REPORT_DIR.mkdir(exist_ok=True)
    with (REPORT_DIR / "terms.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["term", "standard", "type", "variant_found", "component", "key", "line", "en", "zh_Hant"])
        w.writerows(rows)

    md = ["# 詞彙一致性報告", "",
          "「其他」＝既非標準譯法也非已知變體；「候選」為不一致譯文中常見的中文片段，可據此補充 variants 欄位。", "",
          "| 詞彙 | 標準譯法 | 出現 | 符合 | 已知變體 | 其他 | 一致率 | 不一致譯文中的候選片段 |",
          "|---|---|---:|---:|---:|---:|---:|---|"]
    for g, n, st, cands, _ in sorted(summary, key=lambda s: (s[1] - s[2]["ok"]), reverse=True):
        if n == 0:
            continue
        md.append(f"| {g['en']} | {' / '.join(g['ok'])} | {n} | {st['ok']} | {st['variant']} | {st['other']} "
                  f"| {st['ok'] / n:.0%} | {' '.join(cands)} |")
    unused = [g["en"] for g, n, *_ in summary if n == 0]
    if unused:
        md += ["", "未在任何字串中出現的詞彙：" + "、".join(unused)]
    for g, n, st, _, bad in summary:
        if not bad:
            continue
        md += ["", f"## {g['en']} → {' / '.join(g['ok'])}（{len(bad)} 條不一致）", ""]
        if g["note"]:
            md += [f"> {g['note']}", ""]
        for p in bad:
            md.append(f"- `{p.component}:{p.line}` **{p.key}**")
            md.append(f"  - en: {p.en[:300]}")
            md.append(f"  - zh: {p.zh[:300]}")
    (REPORT_DIR / "terms.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"{'詞彙':24}{'出現':>6}{'一致率':>8}")
    for g, n, st, *_ in sorted(summary, key=lambda s: (s[1] - s[2]["ok"]), reverse=True)[:25]:
        if n:
            print(f"{g['en'][:24]:24}{n:6}{st['ok'] / n:8.0%}")
    print("…\n詳見 reports/terms.md、reports/terms.csv")


if __name__ == "__main__":
    main()
