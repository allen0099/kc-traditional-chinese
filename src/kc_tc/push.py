"""把本地修改過的譯文逐條上傳到 Hosted Weblate（需要 API key）。

用法：
    uv run kc-tc push --dry-run            # 只比對，不上傳
    uv run kc-tc push                      # 比對後詢問是否上傳
    uv run kc-tc push admin-ui -y          # 只上傳指定組件，不詢問
    uv run kc-tc push --state approved     # 上傳後標為「已核可」（需要檢閱權限）

檢閱狀態：needs-editing＝需要編輯、translated＝等候檢閱（預設）、approved＝已核可

只上傳 Weblate 現值仍等於上次同步基準的字串；有人在 Weblate 上改過的會列為衝突並略過，
請到網頁的「上傳」頁逐條決定。上傳成功的字串會更新 baseline/，記得 commit。
"""
from __future__ import annotations

import argparse
import sys
import threading
import urllib.error

from .common import RATELIMIT, http_error_text, load_token
from .sync import STATE_LABELS, STATES, compare, local_changes, mark_synced, upload_many

LABEL = {"push": "可上傳", "conflict": "衝突", "synced": "已同步", "missing": "Weblate 無此 key"}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kc-tc push", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("components", nargs="*", help="只處理這些組件")
    ap.add_argument("--dry-run", action="store_true", help="只比對，不上傳")
    ap.add_argument("-y", "--yes", action="store_true", help="不詢問直接上傳")
    ap.add_argument("--state", choices=list(STATES), default="translated", help="上傳後的檢閱狀態（預設 translated＝等候檢閱）")
    args = ap.parse_args(argv)

    token = load_token()
    if not token and not args.dry_run:
        sys.exit("找不到 API key：請在 .env 設定 WEBLATE_TOKEN=…（見 .env.example）")

    changes = local_changes(args.components or None)
    if not changes:
        print("沒有尚未上傳的本地修改。")
        return
    print(f"本地修改 {len(changes)} 條，查詢 Weblate 現值…", flush=True)
    compare(changes, token)

    for status in ("push", "conflict", "missing", "synced"):
        group = [c for c in changes if c.status == status]
        if not group:
            continue
        print(f"\n[{LABEL[status]}] {len(group)} 條")
        if status in ("conflict", "missing"):
            for c in group:
                print(f"  {c.component} {c.key}\n    基準：{c.base}\n    遠端：{c.remote}\n    本地：{c.local}")

    synced = [c for c in changes if c.status == "synced"]
    todo = [c for c in changes if c.status == "push"]
    if args.dry_run:
        print("\n--dry-run：未上傳。")
        return
    for c in synced:   # Weblate 已是本地值，只需更新基準
        mark_synced(c.component, {c.key: c.local})
    if not todo:
        return
    if not args.yes and input(f"\n上傳 {len(todo)} 條並標為「{STATE_LABELS[args.state]}」？[y/N] ").strip().lower() != "y":
        print("已取消。")
        return

    count = {"ok": 0, "fail": 0}
    lock = threading.Lock()

    def done(c, err):
        with lock:
            if err is None:
                count["ok"] += 1
            else:
                count["fail"] += 1
                msg = http_error_text(err) if isinstance(err, urllib.error.HTTPError) else str(err)
                print(f"  ✗ {c.component} {c.key}：{msg}")
            i = count["ok"] + count["fail"]
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}（剩餘額度 {RATELIMIT.get('remaining', '?')}）", flush=True)

    upload_many(todo, token, STATES[args.state], done)
    print(f"\n完成：成功 {count['ok']}，失敗 {count['fail']}。請 commit baseline/ 的變更。")
