"""`debate` 命令：启动 Web 服务并映射到指定端口。"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser

DEFAULT_PORT = 8770


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="debate",
        description="启动 AI 辩论台 Web 服务，在浏览器里配置正反双方、跑多轮辩论并由裁判裁决。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  debate                      # 默认 http://127.0.0.1:8770
  debate --port 9000          # 指定端口
  debate --no-browser         # 不自动打开浏览器
  debate --host 0.0.0.0       # 允许局域网访问（注意本机安全）
""",
    )
    p.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.add_argument("--db", help="SQLite 路径（默认 ~/.quorum/web.db）")
    p.add_argument("--env", help=".env 路径")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    port = args.port

    from .config import sanitize_proxy_env

    sanitize_proxy_env()   # 启动即清洗 no_proxy，避免 [::1] 让所有云端请求失败

    if not _port_free(args.host, port):
        print(f"端口 {port} 已被占用，换一个：debate --port {port + 1}", file=sys.stderr)
        return 2

    import uvicorn

    from .web.app import create_app

    app = create_app(db_path=args.db, env_path=args.env)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{port}"

    print(f"AI 辩论台 → {url}")
    print(f"数据库    → {app.state.store.path}")
    print("Ctrl+C 退出")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(app, host=args.host, port=port, log_level="warning", access_log=False)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
