"""Keycloak 繁體中文翻譯工具。"""
from __future__ import annotations

import sys

COMMANDS = {
    "pull": "從 Hosted Weblate 下載原文與繁中譯文",
    "push": "把本地修改逐條上傳到 Weblate（需要 API key）",
    "terms": "詞彙一致性報告",
    "lint": "格式與用語檢查",
    "serve": "啟動本地網頁（詞彙決策、批次取代、上傳）",
}


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        print("用法：kc-tc <指令> [選項]\n\n指令：")
        for name, desc in COMMANDS.items():
            print(f"  {name:8}{desc}")
        print("\n各指令的選項請用 kc-tc <指令> -h 查看")
        sys.exit(0 if not argv or argv[0] in ("-h", "--help") else 2)

    from importlib import import_module
    try:
        import_module(f"kc_tc.{argv[0]}").main(argv[1:])
    except KeyboardInterrupt:
        sys.exit(130)
