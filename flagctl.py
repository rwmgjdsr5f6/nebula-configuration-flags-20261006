#!/usr/bin/env python3
"""flagctl: 本地布尔功能开关命令行工具。

用法:
    python flagctl.py --db <数据库文件> set <环境名> <键名> <true|false>
    python flagctl.py --db <数据库文件> get <环境名> <键名>
    python flagctl.py --db <数据库文件> unset <环境名> <键名>

仅使用 Python 3 标准库与 SQLite，不依赖网络或第三方包。
"""

import argparse
import os
import sqlite3
import sys

KNOWN_KEYS = frozenset({"new_ui"})

EXIT_OK = 0
EXIT_ERROR = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    env   TEXT NOT NULL,
    key   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (env, key)
)
"""


class FlagError(Exception):
    """携带错误码的业务/存储错误。"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def normalize_env(env):
    """去除环境名两端空白；内部空格保留，大小写敏感。"""
    return env.strip()


def validate_env(env):
    if not env:
        raise FlagError("EMPTY_ENV")


def validate_key(key):
    if key not in KNOWN_KEYS:
        raise FlagError("UNKNOWN_KEY")


def parse_bool(text):
    if text == "true":
        return "true"
    if text == "false":
        return "false"
    raise FlagError("INVALID_BOOL")


def connect(db_path):
    try:
        return sqlite3.connect(db_path)
    except sqlite3.Error:
        raise FlagError("STORAGE_ERROR")


def cmd_set(db_path, env, key, value):
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(SCHEMA)
            conn.execute(
                "INSERT OR REPLACE INTO flags (env, key, value) VALUES (?, ?, ?)",
                (env, key, value),
            )
    except sqlite3.Error:
        raise FlagError("STORAGE_ERROR")
    finally:
        conn.close()
    return value


def cmd_get(db_path, env, key):
    if not os.path.exists(db_path):
        # 父目录不存在属于存储不可达；否则数据库文件不存在时不得创建文件，
        # 视为没有任何已保存的值。
        parent = os.path.dirname(os.path.abspath(db_path))
        if not os.path.isdir(parent):
            raise FlagError("STORAGE_ERROR")
        raise FlagError("VALUE_NOT_SET")
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM flags WHERE env = ? AND key = ?", (env, key)
        ).fetchone()
    except sqlite3.OperationalError as exc:
        # 已存在但缺少 flags 表的数据库视为没有任何已保存的值。
        if "no such table" in str(exc):
            raise FlagError("VALUE_NOT_SET")
        raise FlagError("STORAGE_ERROR")
    except sqlite3.Error:
        raise FlagError("STORAGE_ERROR")
    finally:
        conn.close()
    if row is None:
        raise FlagError("VALUE_NOT_SET")
    return row[0]


def cmd_unset(db_path, env, key):
    if not os.path.exists(db_path):
        # 与 get 一致：父目录不存在属于存储不可达；数据库文件不存在时
        # 不得创建文件，视为没有任何已保存的值。
        parent = os.path.dirname(os.path.abspath(db_path))
        if not os.path.isdir(parent):
            raise FlagError("STORAGE_ERROR")
        raise FlagError("VALUE_NOT_SET")
    conn = connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM flags WHERE env = ? AND key = ?", (env, key)
            )
            if cur.rowcount == 0:
                # 目标记录不存在；事务随异常回滚，不产生任何变更。
                raise FlagError("VALUE_NOT_SET")
    except sqlite3.OperationalError as exc:
        # 已存在但缺少 flags 表的数据库视为没有任何已保存的值，不补建表。
        if "no such table" in str(exc):
            raise FlagError("VALUE_NOT_SET")
        raise FlagError("STORAGE_ERROR")
    except sqlite3.Error:
        raise FlagError("STORAGE_ERROR")
    finally:
        conn.close()
    return "unset"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="flagctl.py", description="本地布尔功能开关命令行工具"
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_set = sub.add_parser("set", help="设置开关值")
    p_set.add_argument("env")
    p_set.add_argument("key")
    p_set.add_argument("value")

    p_get = sub.add_parser("get", help="读取开关值")
    p_get.add_argument("env")
    p_get.add_argument("key")

    p_unset = sub.add_parser("unset", help="撤销开关的直接设置")
    p_unset.add_argument("env")
    p_unset.add_argument("key")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        env = normalize_env(args.env)
        # 输入错误按环境名、键名、布尔值的顺序判断，
        # 且在任何校验失败前不得触碰数据库。
        validate_env(env)
        validate_key(args.key)
        if args.command == "set":
            value = parse_bool(args.value)
            result = cmd_set(args.db, env, args.key, value)
        elif args.command == "get":
            result = cmd_get(args.db, env, args.key)
        else:
            result = cmd_unset(args.db, env, args.key)
    except FlagError as exc:
        sys.stderr.write(exc.code + "\n")
        return EXIT_ERROR
    sys.stdout.write(result + "\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
