"""本地網頁：詞彙決策與批次取代。"""
from __future__ import annotations

import difflib
import re
import threading
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .. import lint
from ..common import (GLOSSARY_CSV, LANG, PROJECT, TRANS_DIR, WEBLATE, Conflict, Pair,
                      load_glossary, load_pairs, save_glossary, split_alts, write_values)
from ..terms import (NgramIndex, Term, TermStat, analyze, group_by_translation, load_terms,
                     norm_key, propose_replacements, term_regex)

HERE = Path(__file__).parent
app = FastAPI(title="kc-tc")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


# ---------------------------------------------------------------- 資料快取

class Store:
    """翻譯檔或詞彙表變動時自動重新載入。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._sig = None

    def _signature(self):
        files = sorted(TRANS_DIR.glob(f"*/{LANG}.properties")) + [GLOSSARY_CSV]
        return tuple((str(f), f.stat().st_mtime_ns) for f in files if f.exists())

    def get(self) -> "Store":
        with self._lock:
            sig = self._signature()
            if sig != self._sig:
                self.pairs = [p for p in load_pairs() if p.zh]
                self.index = NgramIndex(self.pairs)
                self.terms = load_terms()
                self.stats = {k: analyze(t, self.pairs) for k, t in self.terms.items()}
                self._sig = sig
        return self

    def sorted_stats(self, q: str = "") -> list[TermStat]:
        q = q.strip().lower()
        stats = [s for s in self.stats.values()
                 if not q or q in s.term.en.lower() or any(q in x for x in s.term.ok + s.term.var)]
        return sorted(stats, key=lambda s: (-s.inconsistent, s.term.en.lower()))


store = Store()


# ---------------------------------------------------------------- 模板輔助

def hl_en(text: str, en: str | list[str]) -> Markup:
    ens = [en] if isinstance(en, str) else en
    rx = re.compile("|".join(term_regex(e).pattern for e in ens), re.I)
    out, pos = [], 0
    for m in rx.finditer(text):
        out += [escape(text[pos:m.start()]), Markup("<mark>"), escape(m.group()), Markup("</mark>")]
        pos = m.end()
    out.append(escape(text[pos:]))
    return Markup("").join(out)


def hl_zh(text: str, ok: list[str], var: list[str], label: str = "") -> Markup:
    marks = [(x, "ok") for x in ok] + [(x, "var") for x in var]
    if label and label not in ok + var:
        marks.append((label, "cand"))
    if not marks:
        return escape(text)
    marks.sort(key=lambda m: -len(m[0]))
    cls = dict(marks)
    rx = re.compile("|".join(re.escape(m) for m, _ in marks))
    out, pos = [], 0
    for m in rx.finditer(text):
        out += [escape(text[pos:m.start()]), Markup(f'<mark class="{cls[m.group()]}">'),
                escape(m.group()), Markup("</mark>")]
        pos = m.end()
    out.append(escape(text[pos:]))
    return Markup("").join(out)


def diff_html(old: str, new: str) -> tuple[Markup, Markup]:
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    a, b = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            a.append(escape(old[i1:i2]))
            b.append(escape(new[j1:j2]))
            continue
        if i2 > i1:
            a += [Markup("<del>"), escape(old[i1:i2]), Markup("</del>")]
        if j2 > j1:
            b += [Markup("<ins>"), escape(new[j1:j2]), Markup("</ins>")]
    return Markup("").join(a), Markup("").join(b)


def weblate_url(p: Pair) -> str:
    return f"{WEBLATE}/translate/{PROJECT}/{p.component}/{LANG}/?q=" + quote(f'key:"{p.key}"')


templates.env.globals.update(hl_en=hl_en, hl_zh=hl_zh, diff_html=diff_html, weblate_url=weblate_url)


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx)


# ---------------------------------------------------------------- 詞彙決策

@app.get("/")
def index():
    return RedirectResponse("/terms")


@app.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request, sel: str = "", q: str = "", new: str = ""):
    s = store.get()
    detail = term_detail_ctx(s, sel, new) if (sel or new) else None
    tpl = "terms.html" if "hx-request" not in request.headers else "_terms_main.html"
    return render(request, tpl, stats=s.sorted_stats(q), q=q, sel=sel, detail=detail,
                  total_pairs=len(s.pairs))


@app.get("/terms/list", response_class=HTMLResponse)
def terms_list(request: Request, q: str = "", sel: str = ""):
    s = store.get()
    return render(request, "_term_list.html", stats=s.sorted_stats(q), sel=sel)


def term_detail_ctx(s: Store, key: str, new_en: str = "", extra: list[str] = ()) -> dict | None:
    if new_en:
        key = norm_key(new_en)
    t = s.terms.get(key)
    is_new = t is None
    if is_new:
        if not new_en:
            return None
        t = Term(key, new_en.strip(), [], [])
        t.longer = [term_regex(o.en) for o in s.terms.values() if t.rx.search(o.en)]
    ts = analyze(t, s.pairs) if is_new else s.stats[key]
    groups = group_by_translation(ts, s.index, list(extra))
    return {"ts": ts, "t": t, "groups": groups, "is_new": is_new, "extra": "|".join(extra)}


@app.get("/terms/detail", response_class=HTMLResponse)
def term_detail(request: Request, key: str = "", new: str = "", extra: str = ""):
    s = store.get()
    detail = term_detail_ctx(s, key, new, split_alts(extra))
    return render(request, "_term_detail.html", detail=detail)


@app.get("/terms/group", response_class=HTMLResponse)
def term_group(request: Request, key: str, label: str = "", kind: str = "", new: str = "", extra: str = ""):
    s = store.get()
    detail = term_detail_ctx(s, key, new, split_alts(extra))
    group = next((g for g in detail["groups"] if g.label == label and g.kind == kind), None)
    return render(request, "_group_items.html", group=group, t=detail["t"])


@app.post("/terms/save", response_class=HTMLResponse)
def term_save(request: Request, orig: str = Form(""), en: str = Form(...), zh_Hant: str = Form(""),
              variants: str = Form(""), note: str = Form("")):
    en = en.strip()
    new_key = norm_key(en)
    row = {"en": en, "zh_Hant": "|".join(split_alts(zh_Hant)),
           "variants": "|".join(split_alts(variants)), "note": note.strip()}
    rows = load_glossary()
    drop = {orig, new_key} - {""}
    idx = next((i for i, r in enumerate(rows) if norm_key(r["en"]) in drop), None)
    rows = [r for r in rows if norm_key(r["en"]) not in drop]
    if idx is None:
        rows.append(row)
    else:
        rows.insert(min(idx, len(rows)), row)
    save_glossary(rows)
    s = store.get()
    return render(request, "_terms_main.html", stats=s.sorted_stats(), q="", sel=new_key,
                  detail=term_detail_ctx(s, new_key), total_pairs=len(s.pairs), saved=True)


@app.post("/terms/delete", response_class=HTMLResponse)
def term_delete(request: Request, orig: str = Form(...)):
    save_glossary([r for r in load_glossary() if norm_key(r["en"]) != orig])
    s = store.get()
    return render(request, "_terms_main.html", stats=s.sorted_stats(), q="", sel="", detail=None,
                  total_pairs=len(s.pairs))


# ---------------------------------------------------------------- 批次取代

@app.get("/replace", response_class=HTMLResponse)
def replace_page(request: Request, term: list[str] = [], component: str = ""):
    s = store.get()
    chosen = [s.terms[k] for k in term if k in s.terms] or list(s.terms.values())
    pairs = [p for p in s.pairs if not component or p.component == component]
    props = sorted(propose_replacements(chosen, pairs), key=lambda x: (x.pair.component, x.pair.line or 0))
    replaceable = sorted((t for t in s.terms.values() if t.ok and t.var), key=lambda t: t.en.lower())
    components = sorted({p.component for p in s.pairs})
    return render(request, "replace.html", props=props, terms=replaceable, selected=set(term),
                  component=component, components=components)


@app.post("/replace/apply", response_class=HTMLResponse)
async def replace_apply(request: Request):
    form = await request.form()
    ids = form.getlist("pick")
    by_comp: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for i in ids:
        comp, key = form[f"comp_{i}"], form[f"key_{i}"]
        old, new = form[f"old_{i}"], form[f"new_{i}"].replace("\r\n", "\n")
        if old != new:
            by_comp[comp][key] = (old, new)

    applied, errors = [], []
    for comp, changes in by_comp.items():
        try:
            write_values(comp, changes)
            applied += [(comp, k, o, n) for k, (o, n) in changes.items()]
        except Conflict as e:
            errors.append(str(e))

    s = store.get()
    en_of = {(p.component, p.key): p.en for p in s.pairs}
    tw_rules = lint.load_tw_terms()
    results = []
    for comp, key, old, new in applied:
        p = Pair(comp, key, en_of.get((comp, key), ""), new, None)
        issues = [(lv, ck, msg) for lv, ck, msg in lint.check(p, tw_rules) if lv != "info"]
        results.append({"comp": comp, "key": key, "old": old, "new": new, "issues": issues})
    return render(request, "_replace_result.html", results=results, errors=errors)
