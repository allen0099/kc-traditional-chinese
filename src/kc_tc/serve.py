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

    wildcard = args.host in ("0.0.0.0", "::")
    url = f"http://{'127.0.0.1' if wildcard else args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    print(f"kc-tc 網頁：{url}（Ctrl+C 結束）", flush=True)
    if wildcard:
        for ip in _local_ips():
            print(f"          http://{ip}:{args.port}/", flush=True)
        print("注意：網頁沒有登入驗證且可修改檔案，請只在可信任的網路中開放。", flush=True)
    uvicorn.run("kc_tc.web.app:app", host=args.host, port=args.port, reload=args.reload, log_level="warning")


def _local_ips() -> list[str]:
    """本機對外的 IPv4 位址（用於顯示可連線的網址）。"""
    import socket

    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # 不會真的送出封包，只用來取得預設路由的來源位址
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        ips.update(i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))
