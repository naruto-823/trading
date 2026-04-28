"""CLI 入口：命令行同步数据"""

import argparse
import sys

from app.db import SessionLocal, init_db
from app.longbridge.sync import sync_account, sync_all, sync_executions, sync_orders, sync_positions

SYNC_HANDLERS = {
    "all": sync_all,
    "account": sync_account,
    "positions": sync_positions,
    "orders": sync_orders,
    "executions": sync_executions,
}


def main():
    parser = argparse.ArgumentParser(description="AI Trading CLI")
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser("sync", help="同步长桥数据")
    sync_parser.add_argument("--all", action="store_true", help="同步全部数据")
    sync_parser.add_argument("--kind", choices=["account", "positions", "orders", "executions"], help="同步指定类型")

    args = parser.parse_args()

    if args.command == "sync":
        init_db()
        db = SessionLocal()
        try:
            if args.all or not args.kind:
                kind = "all"
            else:
                kind = args.kind

            handler = SYNC_HANDLERS.get(kind)
            if not handler:
                print(f"❌ 不支持的同步类型: {kind}")
                sys.exit(1)

            print(f"🔄 开始同步: {kind}")
            result = handler(db)

            if isinstance(result, list):
                for log in result:
                    status_icon = "✅" if log.status == "success" else "❌"
                    print(f"  {status_icon} {log.kind}: {log.status} ({log.rows_written} rows)")
                    if log.error:
                        print(f"     错误: {log.error[:200]}")
            else:
                status_icon = "✅" if result.status == "success" else "❌"
                print(f"  {status_icon} {result.kind}: {result.status} ({result.rows_written} rows)")
                if result.error:
                    print(f"     错误: {result.error[:200]}")

            print("🏁 同步完成")
        finally:
            db.close()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
