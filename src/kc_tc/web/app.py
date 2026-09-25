"""本地網頁：詞彙決策與批次取代。"""
from __future__ import annotations

import difflib
import re
import threading
import urllib.error
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .. import lint, sync
from ..common import (GLOSSARY_CSV, LANG, PROJECT, RATELIMIT, TRANS_DIR, WEBLATE, Conflict, Pair,
                      load_glossary, load_pairs, load_token, load_values, save_glossary, split_alts,
                      write_values, zh_path)
from ..terms import (NgramIndex, Term, TermStat, analyze, group_by_translation, load_terms,
                     norm_key, propose_replacements, term_regex)

HERE = Path(__file__).parent
app = FastAPI(title="kc-tc")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """擋掉跨站 POST：必須由 htmx 送出（帶 HX-Request 標頭，跨站需 CORS 預檢而不會通過），
    且 Origin 若存在須與 Host 相同。"""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if "hx-request" not in request.headers or (origin and urlsplit(origin).netloc != request.headers.get("host")):
            return PlainTextResponse("拒絕跨站請求", status_code=403)
    return await call_next(request)


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

    def sorted_stats(self, q: str = "") -> dict[str, list[TermStat]]:
        """{"pending": 待處理（含已檢視但有新變動）, "done": 已檢視}"""
        q = q.strip().lower()
        stats = [s for s in self.stats.values()
                 if not q or q in s.term.en.lower() or any(q in x for x in s.term.ok + s.term.var)]
        stats.sort(key=lambda s: (s.review != "changed", -s.inconsistent, s.term.en.lower()))
        return {"pending": [s for s in stats if s.review != "done"],
                "done": [s for s in stats if s.review == "done"]}


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
    if idx is not None:
        row["reviewed"] = rows[idx].get("reviewed", "")
    rows = [r for r in rows if norm_key(r["en"]) not in drop]
    if idx is None:
        rows.append(row)
    else:
        rows.insert(min(idx, len(rows)), row)
    save_glossary(rows)
    s = store.get()
    return render(request, "_terms_main.html", stats=s.sorted_stats(), q="", sel=new_key,
                  detail=term_detail_ctx(s, new_key), total_pairs=len(s.pairs), saved=True)


@app.post("/terms/review", response_class=HTMLResponse)
def term_review(request: Request, orig: str = Form(...), mark: str = Form("1")):
    """標記／取消已檢視；標記時記下當下的不一致條數。"""
    s = store.get()
    ts = s.stats.get(orig)
    rows = load_glossary()
    for r in rows:
        if norm_key(r["en"]) == orig:
            r["reviewed"] = str(ts.inconsistent) if (mark == "1" and ts) else ""
    save_glossary(rows)
    s = store.get()
    return render(request, "_terms_main.html", stats=s.sorted_stats(), q="", sel=orig,
                  detail=term_detail_ctx(s, orig), total_pairs=len(s.pairs))


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
    form = await request.form(max_fields=100_000)   # 每條建議 5 個欄位，預設上限 1000 不夠用
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


# ---------------------------------------------------------------- 上傳到 Weblate

class PushJob:
    """背景上傳工作（同時只允許一個）。"""

    def __init__(self, changes: list[sync.Change], state: int):
        self.changes, self.state = changes, state
        self.done: list[tuple[sync.Change, str, str]] = []   # (change, ok/skip/fail, 訊息)
        self.finished = False
        self.phase = "查詢 Weblate 現值…"

    def run(self, token: str) -> None:
        try:
            # 上傳前再查一次：比對時的遠端值若已改變就略過
            expected = {c.id: c.remote for c in self.changes}
            fresh = sync.compare([sync.Change(c.component, c.key, c.en, c.base, c.local) for c in self.changes], token)
            self.phase = "上傳中…"
            for c, f in zip(self.changes, fresh):
                if f.remote != expected[c.id]:
                    self.done.append((c, "skip", "比對後 Weblate 又被修改，請重新比對"))
                    continue
                if load_values(zh_path(c.component)).get(c.key) != c.local:
                    self.done.append((c, "skip", "本地譯文在比對後有變更，請重新比對"))
                    continue
                try:
                    sync.upload(c, token, self.state)
                    self.done.append((c, "ok", ""))
                except urllib.error.HTTPError as e:
                    self.done.append((c, "fail", f"HTTP {e.code} {e.read()[:200].decode(errors='replace')}"))
                except OSError as e:
                    self.done.append((c, "fail", str(e)))
        except Exception as e:  # noqa: BLE001 — 顯示在網頁上
            self.phase = f"錯誤：{e}"
        finally:
            self.finished = True


PUSH: dict = {"plan": None, "plan_id": "", "job": None}


def push_ctx() -> dict:
    changes = sync.local_changes()
    by_comp = defaultdict(int)
    for c in changes:
        by_comp[c.component] += 1
    return {"n_local": len(changes), "by_comp": dict(sorted(by_comp.items())), "has_token": bool(load_token()),
            "ratelimit": RATELIMIT, "job": PUSH["job"]}


@app.get("/push", response_class=HTMLResponse)
def push_page(request: Request):
    return render(request, "push.html", **push_ctx())


@app.get("/push/local", response_class=HTMLResponse)
def push_local(request: Request, component: str = "", q: str = ""):
    """本地預覽：基準 → 本地的差異，不連線 Weblate。"""
    changes = sync.local_changes([component] if component else None)
    if q:
        ql = q.lower()
        changes = [c for c in changes if any(ql in s.lower() for s in (c.key, c.en, c.base, c.local))]
    changes.sort(key=lambda c: (c.component, c.key))
    return render(request, "_push_local.html", changes=changes, q=q)


@app.post("/push/compare", response_class=HTMLResponse)
def push_compare(request: Request, component: str = Form("")):
    token = load_token()
    changes = sync.local_changes([component] if component else None)
    try:
        sync.compare(changes, token)
    except urllib.error.HTTPError as e:
        return render(request, "_push_plan.html", error=f"查詢 Weblate 失敗：HTTP {e.code}", ratelimit=RATELIMIT)
    synced = [c for c in changes if c.status == "synced"]
    for c in synced:   # Weblate 已是本地值，直接更新基準
        sync.mark_synced(c.component, {c.key: c.local})
    plan = [c for c in changes if c.status in ("push", "conflict", "missing")]
    order = {"conflict": 0, "push": 1, "missing": 2}
    plan.sort(key=lambda c: (order[c.status], c.component, c.key))
    PUSH["plan"], PUSH["plan_id"] = plan, uuid.uuid4().hex
    return render(request, "_push_plan.html", plan=plan, plan_id=PUSH["plan_id"], synced=synced,
                  has_token=bool(token), ratelimit=RATELIMIT, states=sync.STATE_LABELS,
                  remote_states=sync.REMOTE_STATE_LABELS, state_nums=sync.STATES)


@app.post("/push/apply", response_class=HTMLResponse)
async def push_apply(request: Request):
    form = await request.form(max_fields=100_000)
    token = load_token()
    job = PUSH["job"]
    if not token:
        return HTMLResponse('<div class="flash error">找不到 API key，請在 .env 設定 WEBLATE_TOKEN</div>')
    if job and not job.finished:
        return HTMLResponse('<div class="flash error">已有上傳工作進行中</div>')
    if form.get("plan_id") != PUSH["plan_id"] or PUSH["plan"] is None:
        return HTMLResponse('<div class="flash error">比對結果已過期，請重新比對</div>')
    plan = PUSH["plan"]
    picked = [plan[int(i)] for i in form.getlist("pick") if plan[int(i)].status in ("push", "conflict")]
    if not picked:
        return HTMLResponse('<div class="flash error">沒有勾選任何項目</div>')
    job = PushJob(picked, sync.STATES.get(form.get("state", ""), sync.STATES["translated"]))
    PUSH["job"], PUSH["plan"] = job, None
    threading.Thread(target=job.run, args=(token,), daemon=True).start()
    return render(request, "_push_progress.html", job=job, ratelimit=RATELIMIT)


@app.get("/push/progress", response_class=HTMLResponse)
def push_progress(request: Request):
    return render(request, "_push_progress.html", job=PUSH["job"], ratelimit=RATELIMIT)
