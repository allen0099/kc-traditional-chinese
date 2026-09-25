"""從 Hosted Weblate 下載 Keycloak 各組件的英文原文與繁體中文翻譯。

用法：
    uv run kc-tc pull                 # 下載全部組件
    uv run kc-tc pull admin-ui        # 只下載指定組件

本地尚未上傳的修改會保留：Weblate 沒動的直接保留，雙方都改的列為衝突（保留本地值，
上傳頁會標示衝突讓你決定）。基準存於 baseline/<組件>.json；Weblate 上需要編輯的字串
（英文原文改過等）記錄在 baseline/needs_edit.json。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .common import (GLOSSARY_COMPONENT, GLOSSARY_CSV, LANG, MANIFEST, PROJECT,
                    ROOT, TRANS_DIR, api_get, api_list, load_needs_edit, load_token, save_needs_edit)
from .sync import fetch_units, merge_pull, needs_edit_of

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog=f"kc-tc {__name__.rsplit('.', 1)[-1]}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("components", nargs="*", help="只下載這些組件（slug）")
    args = ap.parse_args(argv)

    token = load_token()
    print(f"使用 {'API key' if token else '匿名'} 存取 Weblate")

    comps = api_list(f"projects/{PROJECT}/components/?page_size=100", token)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest.setdefault("components", {})
    needs = load_needs_edit()

    for c in comps:
        slug = c["slug"]
        if args.components and slug not in args.components:
            continue
        if slug == GLOSSARY_COMPONENT:
            pull_glossary(token)
            continue

        src_lang = c["source_language"]["code"]
        d = TRANS_DIR / slug
        d.mkdir(parents=True, exist_ok=True)
        print(f"下載 {slug} …")
        (d / "en.properties").write_bytes(
            api_get(f"translations/{PROJECT}/{slug}/{src_lang}/file/", token))
        try:
            stats = json.loads(api_get(f"translations/{PROJECT}/{slug}/{LANG}/", token))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            print(f"  此組件尚無 {LANG} 翻譯，僅下載原文")
            manifest["components"][slug] = {"name": c["name"], "filemask": c["filemask"],
                                            "translated_percent": 0, "missing": True}
            continue
        res = merge_pull(slug, api_get(f"translations/{PROJECT}/{slug}/{LANG}/file/", token))
        if res.updated:
            print(f"  Weblate 更新 {res.updated} 條")
        if res.kept:
            print(f"  保留本地未上傳的修改 {len(res.kept)} 條")
        if res.conflicts:
            print(f"  ⚠ 衝突 {len(res.conflicts)} 條（雙方都改過，已保留本地值）：{', '.join(res.conflicts[:10])}"
                  + (" …" if len(res.conflicts) > 10 else ""))
        if res.dropped:
            print(f"  ⚠ Weblate 已移除，捨棄本地修改 {len(res.dropped)} 條：{', '.join(res.dropped[:10])}")
        needs[slug] = needs_edit_of(fetch_units(slug, token))
        if needs[slug]:
            print(f"  需要編輯 {len(needs[slug])} 條（通常是英文原文改過）")

        manifest["components"][slug] = {
            "name": c["name"],
            "filemask": c["filemask"],
            "repo_file": stats["filename"],
            "translated_percent": stats["translated_percent"],
            "translated": stats["translated"],
            "total": stats["total"],
            "last_change": stats.get("last_change"),
            "url": stats["translate_url"],
        }
        print(f"  {stats['translated']}/{stats['total']} ({stats['translated_percent']}%)")

    save_needs_edit(needs)
    manifest["pulled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("完成。可用 `git diff` 檢視與上次下載的差異。")


def pull_glossary(token: str | None) -> None:
    """下載 Weblate 詞彙表（TBX），轉成 CSV；若 glossary.csv 不存在則以它初始化。"""
    print("下載 glossary …")
    tbx = api_get(f"translations/{PROJECT}/{GLOSSARY_COMPONENT}/{LANG}/file/", token)
    out_dir = ROOT / "glossary"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "weblate.tbx").write_bytes(tbx)

    rows = []
    for entry in ET.fromstring(tbx).iter("termEntry"):
        terms = {ls.get(XML_LANG): (ls.findtext("tig/term") or "").strip() for ls in entry.iter("langSet")}
        notes = [n.text.strip() for n in entry.iter("note") if n.text and n.text.strip()]
        rows.append({"en": terms.get("en", ""), "zh_Hant": terms.get(LANG, ""),
                     "note": " / ".join(notes).replace("\r", "").replace("\n", " ")})
    rows.sort(key=lambda r: r["en"].lower())

    with (out_dir / "weblate.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["en", "zh_Hant", "note"])
        w.writeheader()
        w.writerows(rows)
    print(f"  {len(rows)} 條詞彙 → glossary/weblate.csv")

    if not GLOSSARY_CSV.exists():
        with GLOSSARY_CSV.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["en", "zh_Hant", "variants", "note"])
            w.writeheader()
            for r in rows:
                if r["en"] and r["zh_Hant"]:
                    w.writerow({"en": r["en"], "zh_Hant": r["zh_Hant"], "variants": "", "note": r["note"]})
        print(f"  已用 Weblate 詞彙表初始化 {GLOSSARY_CSV.name}（之後不會再覆蓋）")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
