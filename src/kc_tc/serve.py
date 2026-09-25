"""啟動本地網頁（詞彙決策、批次取代）。

用法：
    uv run kc-tc serve                 # http://127.0.0.1:8765
    uv run kc-tc serve --port 9000 --no-browser
"""
from __future__ import annotations

import argparse
import threading
import webbrowser


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kc-tc serve", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="不要自動開啟瀏覽器")
    ap.add_argument("--reload", action="store_true", help="程式碼變動時自動重啟（開發用）")
    args = ap.parse_args(argv)

    import uvicorn

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    print(f"kc-tc 網頁：{url}（Ctrl+C 結束）")
    uvicorn.run("kc_tc.web.app:app", host=args.host, port=args.port, reload=args.reload, log_level="warning")
