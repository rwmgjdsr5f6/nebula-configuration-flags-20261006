#!/usr/bin/env python3
"""布尔功能开关命令行工具。

用法:
    python flagctl.py --db <数据库文件> set <环境名> <键名> <true|false>
    python flagctl.py --db <数据库文件> get <环境名> <键名>

成功时标准输出仅包含 true 或 false 及换行，退出码为 0。
失败时标准输出为空，标准错误仅包含错误码及换行，退出码为 2。
"""

import argparse
import os
import sqlite3
import sys

KNOWN_KEYS = frozenset({"new_ui"})

CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS flags ("
    " env TEXT NOT NULL,"
    " key TEXT NOT NULL,"
    " value TEXT NOT NULL,"
    " PRIMARY KEY (env, key)"
    ")"
)


def fail(code):
    """按约定输出错误码并以退出码 2 结束进程。"""
    sys.stderr.write(code + "\n")
    sys.exit(2)


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="flagctl.py")
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set")
    set_parser.add_argument("env")
    set_parser.add_argument("key")
    set_parser.add_argument("value")

    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("env")
    get_parser.add_argument("key")

    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # 输入校验顺序:环境名、键名、布尔值;任一失败都不得触碰数据库。
    env = args.env.strip()
    if not env:
        fail("EMPTY_ENV")
    if args.key not in KNOWN_KEYS:
        fail("UNKNOWN_KEY")
    if args.command == "set":
        if args.value not in ("true", "false"):
            fail("INVALID_BOOL")
        value = args.value
    else:
        # 读取时数据库文件必须已存在,且不得创建文件。
        if not os.path.isfile(args.db):
            fail("STORAGE_ERROR")

    try:
        conn = sqlite3.connect(args.db)
        try:
            if args.command == "set":
                conn.execute(CREATE_TABLE_SQL)
                conn.execute(
                    "INSERT OR REPLACE INTO flags (env, key, value)"
                    " VALUES (?, ?, ?)",
                    (env, args.key, value),
                )
                conn.commit()
            else:
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master"
                    " WHERE type = 'table' AND name = 'flags'"
                ).fetchone()
                if table is None:
                    fail("VALUE_NOT_SET")
                row = conn.execute(
                    "SELECT value FROM flags WHERE env = ? AND key = ?",
                    (env, args.key),
                ).fetchone()
                if row is None:
                    fail("VALUE_NOT_SET")
                value = row[0]
        finally:
            conn.close()
    except sqlite3.Error:
        fail("STORAGE_ERROR")

    sys.stdout.write(value + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
