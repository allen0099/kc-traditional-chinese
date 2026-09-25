"""把本地修改過的譯文逐條上傳到 Hosted Weblate（需要 API key）。

用法：
    uv run kc-tc push --dry-run            # 只比對，不上傳
    uv run kc-tc push                      # 比對後詢問是否上傳
    uv run kc-tc push admin-ui -y          # 只上傳指定組件，不詢問
    uv run kc-tc push --state approved     # 上傳後標為「已核准」（需要審核權限）

只上傳 Weblate 現值仍等於上次同步基準的字串；有人在 Weblate 上改過的會列為衝突並略過，
請到網頁的「上傳」頁逐條決定。上傳成功的字串會更新 baseline/，記得 commit。
"""
from __future__ import annotations

import argparse
import sys
import urllib.error

from .common import RATELIMIT, load_token
from .sync import STATES, compare, local_changes, mark_synced, upload

LABEL = {"push": "可上傳", "conflict": "衝突", "synced": "已同步", "missing": "Weblate 無此 key"}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kc-tc push", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("components", nargs="*", help="只處理這些組件")
    ap.add_argument("--dry-run", action="store_true", help="只比對，不上傳")
    ap.add_argument("-y", "--yes", action="store_true", help="不詢問直接上傳")
    ap.add_argument("--state", choices=list(STATES), default="translated", help="上傳後的狀態（預設 translated）")
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
    if not args.yes and input(f"\n上傳 {len(todo)} 條並標為 {args.state}？[y/N] ").strip().lower() != "y":
        print("已取消。")
        return

    ok = fail = 0
    for i, c in enumerate(todo, 1):
        try:
            upload(c, token, STATES[args.state])
            ok += 1
        except urllib.error.HTTPError as e:
            fail += 1
            print(f"  ✗ {c.component} {c.key}：HTTP {e.code} {e.read()[:200].decode(errors='replace')}")
        if i % 50 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}（剩餘額度 {RATELIMIT.get('remaining', '?')}）", flush=True)
    print(f"\n完成：成功 {ok}，失敗 {fail}。請 commit baseline/ 的變更。")
