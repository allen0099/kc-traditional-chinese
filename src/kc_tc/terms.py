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
from dataclasses import dataclass, field
from functools import lru_cache

from .common import CJK, REPORT_DIR, Pair, load_glossary, load_pairs, split_alts


# ---------------------------------------------------------------- 比對工具

@lru_cache(maxsize=None)
def term_regex(term: str) -> re.Pattern:
    words = [re.escape(w) for w in term.split()]
    body = r"[\s-]+".join(words)
    return re.compile(rf"(?<![\w-]){body}(?:s|es)?(?![\w-])", re.I)


def norm_key(en: str) -> str:
    """大小寫、單複數相同的詞彙視為同一個。"""
    return re.sub(r"(es|s)$", "", en.strip().lower())


def ngrams(s: str, lo: int = 2, hi: int = 4) -> set[str]:
    out = set()
    for run in re.findall(f"[{CJK}]+", s):
        for n in range(lo, hi + 1):
            out.update(run[i:i + n] for i in range(len(run) - n + 1))
    return out


class NgramIndex:
    """全部譯文的 n-gram 文件頻率，用來計算候選譯法的特殊性。"""

    def __init__(self, pairs: list[Pair]):
        self.total = len(pairs)
        self.df = Counter()
        for p in pairs:
            self.df.update(ngrams(p.zh))

    def candidates(self, matched: list[Pair], top: int = 10) -> list[tuple[str, int]]:
        """找出在 matched 譯文中特別常見的中文片段（候選譯法），回傳 [(片段, 條數)]。"""
        df = Counter()
        for p in matched:
            df.update(ngrams(p.zh))
        n = len(matched)
        scored = []
        for g, c in df.items():
            if c < 2 and n > 3:
                continue
            scored.append((g, c, c / n * math.log(self.total / self.df[g]) * (1 + 0.15 * len(g))))
        scored.sort(key=lambda x: (-round(x[2], 9), x[0]))
        # 去掉被更長且同樣頻率的片段涵蓋者
        picked: list[tuple[str, int, float]] = []
        for g, c, s in scored:
            if any(g in pg and c <= pc for pg, pc, _ in picked):
                continue
            picked = [x for x in picked if not (x[0] in g and x[1] <= c)]
            picked.append((g, c, s))
            if len(picked) >= top:
                break
        return [(g, c) for g, c, _ in picked]


# ---------------------------------------------------------------- 詞彙分析

@dataclass
class Term:
    key: str
    en: str
    ok: list[str]
    var: list[str]
    note: str = ""
    longer: list[re.Pattern] = field(default_factory=list)

    @property
    def rx(self) -> re.Pattern:
        return term_regex(self.en)

    def matches(self, en: str) -> bool:
        for lrx in self.longer:
            en = lrx.sub(" ", en)
        return bool(self.rx.search(en))

    def classify(self, zh: str) -> tuple[str, list[str]]:
        """回傳 ("ok" | "variant" | "other", 命中的譯法)"""
        if hit := [x for x in self.ok if x in zh]:
            return "ok", hit
        if hit := [v for v in self.var if v in zh]:
            return "variant", hit
        return "other", []


def load_terms(rows: list[dict] | None = None) -> dict[str, Term]:
    terms: dict[str, Term] = {}
    for r in rows if rows is not None else load_glossary():
        k = norm_key(r["en"])
        t = terms.setdefault(k, Term(k, r["en"].strip(), [], [], r.get("note") or ""))
        t.ok += [x for x in split_alts(r.get("zh_Hant")) if x not in t.ok]
        t.var += [x for x in split_alts(r.get("variants")) if x not in t.var]
    # 較長的詞彙優先：檢查 "client" 時先遮蔽 "client scope"
    for t in terms.values():
        t.longer = [term_regex(o.en) for o in terms.values()
                    if o.key != t.key and t.rx.search(o.en)]
    return terms


@dataclass
class Group:
    label: str            # 中文片段
    kind: str             # ok / variant / candidate / rest
    pairs: list[Pair]


@dataclass
class TermStat:
    term: Term
    matched: list[Pair]
    stat: Counter

    @property
    def n(self) -> int:
        return len(self.matched)

    @property
    def consistency(self) -> float:
        return self.stat["ok"] / self.n if self.n else 1.0

    @property
    def inconsistent(self) -> int:
        return self.n - self.stat["ok"]


def analyze(term: Term, pairs: list[Pair]) -> TermStat:
    matched = [p for p in pairs if p.zh and term.matches(p.en)]
    stat = Counter(term.classify(p.zh)[0] for p in matched)
    return TermStat(term, matched, stat)


def group_by_translation(ts: TermStat, index: NgramIndex, extra: list[str] = ()) -> list[Group]:
    """依目前譯法把字串分組：標準譯法 → 已知變體 → 手動指定片段 → 自動候選 → 其他。"""
    t = ts.term
    labels = [(x, "ok") for x in t.ok] + [(x, "variant") for x in t.var]
    labels += [(x, "candidate") for x in extra if x not in t.ok + t.var]
    rest = [p for p in ts.matched if not any(lb in p.zh for lb, _ in labels)]
    known = {lb for lb, _ in labels}
    # 樣本少時容易抓到重複句型的片段（例如兩句幾乎相同的說明文字），因此要求足夠的出現次數與覆蓋率
    min_count = max(3, round(len(rest) * 0.2))
    for g, c in index.candidates(rest, 8) if len(rest) >= 3 else []:
        if c >= min_count and not any(g in k or k in g for k in known):
            labels.append((g, "candidate"))
            known.add(g)

    groups = {lb: Group(lb, kind, []) for lb, kind in labels}
    other = Group("", "rest", [])
    for p in ts.matched:
        for lb, _ in labels:
            if lb in p.zh:
                groups[lb].pairs.append(p)
                break
        else:
            other.pairs.append(p)
    out = []
    for g in groups.values():
        if g.kind == "candidate" and len(g.pairs) < 2 and g.label not in extra:
            other.pairs += g.pairs      # 只剩 1 條的自動候選多半是雜訊
        elif g.pairs or g.kind in ("ok", "variant"):
            out.append(g)
    return out + ([other] if other.pairs else [])


# ---------------------------------------------------------------- 批次取代

@dataclass
class Proposal:
    pair: Pair
    new: str
    terms: list[str]     # 觸發取代的詞彙
    warnings: list[str]


def propose_replacements(terms: list[Term], pairs: list[Pair]) -> list[Proposal]:
    """依詞彙表把已知變體取代為標準譯法（每個詞彙取第一個標準譯法）。"""
    by_pair: dict[tuple[str, str], Proposal] = {}
    for t in terms:
        if not t.ok or not t.var:
            continue
        target = t.ok[0]
        variants = sorted(t.var, key=len, reverse=True)
        for p in pairs:
            if not p.zh or not t.matches(p.en):
                continue
            key = (p.component, p.key)
            cur = by_pair[key].new if key in by_pair else p.zh
            if any(x in cur for x in t.ok) and not any(v in cur for v in variants):
                continue
            new = cur
            for v in variants:
                if v in target and target in new:
                    continue      # 例如變體「客戶」是標準「客戶端」的一部分，避免重複取代
                new = new.replace(v, target)
            if new == cur:
                continue
            prop = by_pair.setdefault(key, Proposal(p, p.zh, [], []))
            prop.new = new
            prop.terms.append(t.en)
            en_count = len(t.rx.findall(p.en))
            zh_count = new.count(target)
            if zh_count > en_count:
                prop.warnings.append(f"「{target}」出現 {zh_count} 次，但原文「{t.en}」只有 {en_count} 次，可能過度取代")
    return list(by_pair.values())


# ---------------------------------------------------------------- CLI

def explore(term: str, pairs: list[Pair], limit: int) -> None:
    rx = term_regex(term)
    matched = [p for p in pairs if rx.search(p.en)]
    print(f"「{term}」出現在 {len(matched)} 條已翻譯字串中\n")
    if not matched:
        return
    print("候選譯法（出現次數／覆蓋率）：")
    for g, c in NgramIndex(pairs).candidates(matched, 15):
        print(f"  {g:10}\t{c:4}\t{c / len(matched):5.0%}")
    print(f"\n例句（前 {limit} 條）：")
    for p in matched[:limit]:
        print(f"  [{p.component}] {p.key}\n    en: {p.en[:160]}\n    zh: {p.zh[:160]}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kc-tc terms", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--term", help="探索單一英文詞彙目前的譯法")
    ap.add_argument("-n", type=int, default=15, help="--term 模式顯示的例句數")
    ap.add_argument("-c", "--component", action="append", help="只分析指定組件（可重複）")
    args = ap.parse_args(argv)

    pairs = [p for p in load_pairs(args.component) if p.zh]
    if args.term:
        explore(args.term, pairs, args.n)
        return

    index = NgramIndex(pairs)
    stats = sorted((analyze(t, pairs) for t in load_terms().values()),
                   key=lambda s: (-s.inconsistent, s.term.en.lower()))

    rows = []
    for s in stats:
        for p in s.matched:
            kind, hit = s.term.classify(p.zh)
            if kind != "ok":
                rows.append([s.term.en, "|".join(s.term.ok), kind, "|".join(hit),
                             p.component, p.key, p.line, p.en, p.zh])

    REPORT_DIR.mkdir(exist_ok=True)
    with (REPORT_DIR / "terms.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["term", "standard", "type", "variant_found", "component", "key", "line", "en", "zh_Hant"])
        w.writerows(rows)

    md = ["# 詞彙一致性報告", "",
          "「其他」＝既非標準譯法也非已知變體；「候選」為不一致譯文中常見的中文片段，可據此補充 variants 欄位。", "",
          "| 詞彙 | 標準譯法 | 出現 | 符合 | 已知變體 | 其他 | 一致率 | 不一致譯文中的候選片段 |",
          "|---|---|---:|---:|---:|---:|---:|---|"]
    for s in stats:
        if not s.n:
            continue
        bad = [p for p in s.matched if s.term.classify(p.zh)[0] != "ok"]
        cands = " ".join(f"{g}({c})" for g, c in index.candidates(bad, 5)) if len(bad) >= 2 else ""
        md.append(f"| {s.term.en} | {' / '.join(s.term.ok)} | {s.n} | {s.stat['ok']} | {s.stat['variant']} "
                  f"| {s.stat['other']} | {s.consistency:.0%} | {cands} |")
    unused = [s.term.en for s in stats if not s.n]
    if unused:
        md += ["", "未在任何字串中出現的詞彙：" + "、".join(unused)]
    for s in stats:
        bad = [p for p in s.matched if s.term.classify(p.zh)[0] != "ok"]
        if not bad:
            continue
        md += ["", f"## {s.term.en} → {' / '.join(s.term.ok)}（{len(bad)} 條不一致）", ""]
        if s.term.note:
            md += [f"> {s.term.note}", ""]
        for p in bad:
            md.append(f"- `{p.component}:{p.line}` **{p.key}**")
            md.append(f"  - en: {p.en[:300]}")
            md.append(f"  - zh: {p.zh[:300]}")
    (REPORT_DIR / "terms.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"{'詞彙':24}{'出現':>6}{'一致率':>8}")
    for s in stats[:25]:
        if s.n:
            print(f"{s.term.en[:24]:24}{s.n:6}{s.consistency:8.0%}")
    print("…\n詳見 reports/terms.md、reports/terms.csv")
