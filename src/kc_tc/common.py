"""共用設定、Weblate API 存取與 .properties 解析。"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path



def _find_root() -> Path:
    """工作區根目錄：環境變數 KC_TC_ROOT > 目前目錄往上找 pyproject.toml > 套件原始碼所在專案。"""
    if env := os.environ.get("KC_TC_ROOT"):
        return Path(env).resolve()
    for d in (Path.cwd(), *Path.cwd().parents):
        if (d / "pyproject.toml").exists() and (d / "data" / "tw_terms.tsv").exists():
            return d
    return Path(__file__).resolve().parents[2]


ROOT = _find_root()
WEBLATE = "https://hosted.weblate.org"
PROJECT = "keycloak"
LANG = "zh_Hant"
GLOSSARY_COMPONENT = "glossary"

TRANS_DIR = ROOT / "translations"
REPORT_DIR = ROOT / "reports"
DATA_DIR = ROOT / "data"
GLOSSARY_CSV = ROOT / "glossary.csv"
MANIFEST = TRANS_DIR / "manifest.json"


# ---------------------------------------------------------------- Weblate API

def load_token() -> str | None:
    """從環境變數 WEBLATE_TOKEN 或專案根目錄 .env 讀取 API key。"""
    token = os.environ.get("WEBLATE_TOKEN")
    env = ROOT / ".env"
    if not token and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("WEBLATE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip("'\"")
    return token or None


def api_get(url: str, token: str | None = None, retries: int = 5) -> bytes:
    if not url.startswith("http"):
        url = f"{WEBLATE}/api/{url.lstrip('/')}"
    headers = {"User-Agent": "kc-zh-Hant-tools/1.0"}
    if token:
        headers["Authorization"] = f"Token {token}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After", "30"))
                print(f"  觸發速率限制，等待 {wait} 秒…", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def api_list(url: str, token: str | None = None) -> list[dict]:
    """取得分頁列表的所有結果。"""
    results = []
    while url:
        data = json.loads(api_get(url, token))
        results.extend(data["results"])
        url = data.get("next")
    return results


# ---------------------------------------------------------------- .properties

@dataclass
class Entry:
    key: str
    value: str       # 檔案中的原始值（續行已合併，跳脫字元保留）
    line: int        # 起始行號（1-based）
    end: int = 0     # 結束行號（含），有續行時大於 line
    raw_key: str = ""  # 檔案中的原始 key（保留跳脫）


_KEY_SEP = re.compile(r"(?<!\\)[=:]|(?<!\\)\s")


def parse_properties(path: Path) -> dict[str, Entry]:
    """解析 Java .properties（UTF-8），保留原始跳脫形式以便檢查與回寫。"""
    entries: dict[str, Entry] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        start = i
        line = lines[i].lstrip()
        i += 1
        if not line or line[0] in "#!":
            continue
        # 行尾奇數個反斜線 = 續行
        while _continues(line) and i < len(lines):
            line = line[:-1] + lines[i].lstrip()
            i += 1
        m = _KEY_SEP.search(line)
        if m is None:
            key, value = line, ""
        else:
            key = line[: m.start()]
            rest = line[m.start():].lstrip()
            if rest[:1] in ("=", ":"):
                rest = rest[1:].lstrip()
            value = rest
        entries[key.replace("\\", "")] = Entry(key.replace("\\", ""), value, start + 1, i, key)
    return entries


def _continues(line: str) -> bool:
    n = len(line) - len(line.rstrip("\\"))
    return n % 2 == 1


def unescape(value: str) -> str:
    """把 \\uXXXX、\\n 等跳脫轉成實際字元（僅供閱讀/比對用）。"""
    def rep(m: re.Match) -> str:
        s = m.group(1)
        if s.startswith("u"):
            return chr(int(s[1:], 16))
        return {"n": "\n", "t": "\t", "r": "\r", "f": "\f"}.get(s, s)
    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", rep, value)


def escape(value: str) -> str:
    """unescape 的反向操作，採用與 Weblate 相同的 UTF-8 形式（不轉 \\uXXXX）。"""
    s = value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    if s[:1] == " ":
        s = "\\" + s
    return s


class Conflict(Exception):
    """檔案中的值已被其他地方修改。"""


def write_values(component: str, changes: dict[str, tuple[str, str]]) -> None:
    """把 {key: (舊值, 新值)} 寫回 zh_Hant.properties（值皆為 unescape 後的形式）。

    只替換該 key 所在的行，其餘內容保持不變；若舊值與檔案中不符則拋出 Conflict。
    """
    path = TRANS_DIR / component / f"{LANG}.properties"
    entries = parse_properties(path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for key, (old, new) in changes.items():
        e = entries.get(key)
        if e is None:
            raise Conflict(f"{component}: 找不到 {key}")
        if unescape(e.value) != old:
            raise Conflict(f"{component}: {key} 已被修改，請重新整理")
        lines[e.line - 1:e.end] = [f"{e.raw_key}={escape(new)}"] + [None] * (e.end - e.line)
    out = "\n".join(l for l in lines if l is not None)
    path.write_text(out + ("\n" if text.endswith("\n") else ""), encoding="utf-8")


# ---------------------------------------------------------------- 翻譯資料

@dataclass
class Pair:
    component: str
    key: str
    en: str
    zh: str | None   # None 表示尚未翻譯
    line: int | None


def components() -> list[str]:
    return sorted(p.name for p in TRANS_DIR.iterdir() if p.is_dir())


def load_pairs(only: list[str] | None = None) -> list[Pair]:
    pairs = []
    for comp in components():
        if only and comp not in only:
            continue
        en = parse_properties(TRANS_DIR / comp / "en.properties")
        zh_path = TRANS_DIR / comp / f"{LANG}.properties"
        zh = parse_properties(zh_path) if zh_path.exists() else {}
        for key, e in en.items():
            z = zh.get(key)
            pairs.append(Pair(comp, key, unescape(e.value),
                              unescape(z.value) if z else None,
                              z.line if z else None))
    return pairs


def load_glossary() -> list[dict]:
    if not GLOSSARY_CSV.exists():
        return []
    with GLOSSARY_CSV.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("en", "").strip() and not r["en"].startswith("#")]


GLOSSARY_FIELDS = ["en", "zh_Hant", "variants", "note"]


def save_glossary(rows: list[dict]) -> None:
    with GLOSSARY_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GLOSSARY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def split_alts(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split("|") if x.strip()]


CJK = r"㐀-䶿一-鿿豈-﫿"
