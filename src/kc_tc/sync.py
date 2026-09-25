"""本地譯文與 Weblate 的三方比對（基準、本地、Weblate 現值）。"""
from __future__ import annotations

from dataclasses import dataclass

from .common import (LANG, PROJECT, TRANS_DIR, api_list, api_request, components, load_baseline,
                     load_values, parse_properties, save_baseline, unescape, write_values, zh_path)

# Weblate unit 狀態
STATES = {"translated": 20, "approved": 30}


@dataclass
class Change:
    component: str
    key: str
    en: str
    base: str          # 上次同步時的值
    local: str         # 本地現值
    remote: str | None = None   # Weblate 現值（未查詢時為 None）
    unit_url: str = ""
    web_url: str = ""
    status: str = "local"       # local（未比對）/ push / conflict / synced / missing

    @property
    def id(self) -> str:
        return f"{self.component}\x1f{self.key}"


def local_changes(only: list[str] | None = None) -> list[Change]:
    """本地譯文與基準不同的字串（不需要連線）。"""
    out = []
    for comp in components():
        if only and comp not in only:
            continue
        base = load_baseline(comp)
        if base is None:
            continue
        en = {k: unescape(e.value) for k, e in parse_properties(TRANS_DIR / comp / "en.properties").items()}
        for key, val in load_values(zh_path(comp)).items():
            if val != base.get(key, ""):
                out.append(Change(comp, key, en.get(key, ""), base.get(key, ""), val))
    return out


def fetch_units(component: str, token: str | None) -> dict[str, dict]:
    """{key: unit}，unit 端點 page_size 最大 10000，通常 1 次請求即可。"""
    units = api_list(f"translations/{PROJECT}/{component}/{LANG}/units/?page_size=10000", token)
    return {u["context"]: u for u in units}


def compare(changes: list[Change], token: str | None) -> list[Change]:
    """查詢 Weblate 現值並判斷每條的狀態。"""
    for comp in sorted({c.component for c in changes}):
        units = fetch_units(comp, token)
        for c in changes:
            if c.component != comp:
                continue
            u = units.get(c.key)
            if u is None:
                c.status, c.remote = "missing", None
                continue
            c.remote = (u["target"] or [""])[0]
            c.unit_url, c.web_url = u["url"], u["web_url"]
            if c.remote == c.local:
                c.status = "synced"
            elif c.remote == c.base:
                c.status = "push"
            else:
                c.status = "conflict"
    return changes


def upload(c: Change, token: str, state: int) -> None:
    """更新單一字串並同步基準。c.status 必須是 push（或使用者決定覆蓋的 conflict）。"""
    api_request(c.unit_url, token, method="PATCH", data={"target": [c.local], "state": state})
    mark_synced(c.component, {c.key: c.local})


def mark_synced(component: str, values: dict[str, str]) -> None:
    base = load_baseline(component) or {}
    base.update(values)
    save_baseline(component, base)


# ---------------------------------------------------------------- pull 合併

@dataclass
class MergeResult:
    kept: list[str]        # 保留本地修改（Weblate 沒動）
    conflicts: list[str]   # 雙方都改：保留本地值，基準維持舊值，上傳時會標為衝突
    dropped: list[str]     # Weblate 已沒有這個 key，本地修改捨棄
    updated: int           # Weblate 端更新、直接套用的條數


def merge_pull(component: str, remote_bytes: bytes) -> MergeResult:
    """把 Weblate 下載的檔案與本地未上傳的修改合併後寫入。"""
    path = zh_path(component)
    base = load_baseline(component)
    local = load_values(path)
    path.write_bytes(remote_bytes)
    remote = load_values(path)
    res = MergeResult([], [], [], 0)

    if base is None:   # 第一次：直接以 Weblate 為準
        save_baseline(component, remote)
        return res

    reapply: dict[str, tuple[str, str]] = {}
    new_base = dict(remote)
    for key, lv in local.items():
        bv = base.get(key, "")
        rv = remote.get(key)
        if lv == bv:
            continue                          # 本地沒改
        if rv is None:
            res.dropped.append(key)
        elif rv == lv:
            continue                          # 已同步
        elif rv == bv:
            reapply[key] = (rv, lv)
            res.kept.append(key)
        else:
            reapply[key] = (rv, lv)
            new_base[key] = bv                # 維持舊基準，讓上傳時判為衝突
            res.conflicts.append(key)
    res.updated = sum(1 for k, rv in remote.items() if rv != base.get(k, "") and local.get(k) == base.get(k, ""))
    if reapply:
        write_values(component, reapply, path)
    save_baseline(component, new_base)
    return res
