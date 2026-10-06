#!/usr/bin/env python3
"""flagctl: 本地布尔功能开关命令行工具。

用法:
    python flagctl.py --db <数据库文件> set <环境名> <键名> <true|false>
    python flagctl.py --db <数据库文件> get <环境名> <键名>
    python flagctl.py --db <数据库文件> unset <环境名> <键名>
    python flagctl.py --db <数据库文件> list <环境名>
    python flagctl.py --db <数据库文件> diff <左环境名> <右环境名>

仅使用 Python 3 标准库与 SQLite，不依赖网络或第三方包。
"""

import argparse
import contextlib
import json
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


@contextlib.contextmanager
def open_flag_store(db_path):
    """get/unset/list 共用的存储访问入口，统一核对存储状态。

    不创建数据库文件、不补建 flags 表，存储状态的分类只在此维护一份：

    * 父目录不存在或连接失败 -> STORAGE_ERROR；
    * 父目录存在但数据库文件缺失 -> VALUE_NOT_SET；
    * 有效库缺少 flags 表 -> VALUE_NOT_SET；
    * 其他 SQLite 错误 -> STORAGE_ERROR。
    """
    if not os.path.exists(db_path):
        parent = os.path.dirname(os.path.abspath(db_path))
        if not os.path.isdir(parent):
            raise FlagError("STORAGE_ERROR")
        raise FlagError("VALUE_NOT_SET")
    conn = connect(db_path)
    try:
        try:
            yield conn
        except sqlite3.OperationalError as exc:
            # 已存在但缺少 flags 表的数据库视为没有任何已保存的值。
            if "no such table" in str(exc):
                raise FlagError("VALUE_NOT_SET")
            raise FlagError("STORAGE_ERROR")
        except sqlite3.Error:
            raise FlagError("STORAGE_ERROR")
    finally:
        conn.close()


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
    """读取目标记录的布尔值，纯只读、不修复异常数据。

    目标记录不存在报 VALUE_NOT_SET；记录存在但值不是严格的文本
    true/false 时与 list 一样视为存储数据损坏，报 STORAGE_ERROR：
    不做大小写转换、不去除空白、不输出原值。只检查目标记录，其他
    环境的异常值不在本次读取范围内。
    """
    with open_flag_store(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM flags WHERE env = ? AND key = ?", (env, key)
        ).fetchone()
    if row is None:
        raise FlagError("VALUE_NOT_SET")
    if row[0] != "true" and row[0] != "false":
        raise FlagError("STORAGE_ERROR")
    return row[0]


def cmd_unset(db_path, env, key):
    with open_flag_store(db_path) as conn:
        # 不补建 flags 表：缺表与没有目标记录一样视为值未设置（由
        # open_flag_store 统一分类）。直接按主键删除，以 rowcount 是否
        # 为 0 区分记录是否存在，无论原值是 true 还是 false 都删除该行。
        with conn:
            cur = conn.execute(
                "DELETE FROM flags WHERE env = ? AND key = ?", (env, key)
            )
    if cur.rowcount == 0:
        raise FlagError("VALUE_NOT_SET")
    return "unset"


def cmd_list(db_path, env):
    """列出目标环境已保存的已知键直接设置，返回 {键名: 布尔值}。

    纯只读查询：不创建数据库文件、目录或 flags 表，不改动任何记录。
    存储状态分类完全复用 open_flag_store 维护的唯一一份规则，只是把
    “没有数据”的信号（VALUE_NOT_SET：数据库文件缺失或有效库缺少
    flags 表）翻译为 list 语义下的空结果；其余 STORAGE_ERROR 原样
    向上传递。

    未知键不出现在结果中；已知键若存有 true/false 之外的值，
    报 STORAGE_ERROR，不输出部分结果。
    """
    try:
        with open_flag_store(db_path) as conn:
            rows = conn.execute(
                "SELECT key, value FROM flags WHERE env = ?", (env,)
            ).fetchall()
    except FlagError as exc:
        # 对 list 而言，“没有任何已保存的值”就是空结果而非错误。
        if exc.code == "VALUE_NOT_SET":
            return {}
        raise
    result = {}
    for key, value in rows:
        if key not in KNOWN_KEYS:
            continue
        if value == "true":
            result[key] = True
        elif value == "false":
            result[key] = False
        else:
            raise FlagError("STORAGE_ERROR")
    return result


def cmd_diff(db_path, left_env, right_env):
    """比较两个环境的已知键直接设置，只返回设置不同的键。

    返回 {键名: {"left": 左侧值, "right": 右侧值}}，已设置为 JSON 布尔值，
    未设置为 None（序列化为 null），false 与未设置严格区分；两侧都未设置
    或布尔值相同的键不出现在结果中。只比较直接设置，不涉及默认值或继承。

    纯只读：存储访问与数据校验完全复用 cmd_list 维护的唯一一份规则——
    数据库文件缺失或有效库缺少 flags 表视为全部未设置，任一目标环境的已知
    键存有 true/false 之外的值报 STORAGE_ERROR 且不输出部分差异；其他
    环境及未知键的记录不参与比较。自身不新增任何存储规则，也不创建或
    修改任何文件、目录、表或记录。
    """
    left = cmd_list(db_path, left_env)
    right = cmd_list(db_path, right_env)
    diff = {}
    for key in KNOWN_KEYS:
        lval = left.get(key)
        rval = right.get(key)
        # None 只与 None 相等，false 与未设置（None）自然区分开。
        if lval == rval:
            continue
        diff[key] = {"left": lval, "right": rval}
    return diff


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

    p_list = sub.add_parser("list", help="列出环境已保存的开关")
    p_list.add_argument("env")

    p_diff = sub.add_parser("diff", help="比较两个环境的开关差异")
    p_diff.add_argument("left_env")
    p_diff.add_argument("right_env")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "diff":
            # diff 只接收两个环境名，按输入顺序作为左右两侧；两个环境名
            # 都在访问存储之前校验，任一为空或全空白即报 EMPTY_ENV。
            left_env = normalize_env(args.left_env)
            right_env = normalize_env(args.right_env)
            validate_env(left_env)
            validate_env(right_env)
            result = json.dumps(
                cmd_diff(args.db, left_env, right_env), sort_keys=True
            )
            sys.stdout.write(result + "\n")
            return EXIT_OK
        env = normalize_env(args.env)
        # 输入错误按环境名、键名、布尔值的顺序判断，
        # 且在任何校验失败前不得触碰数据库。
        validate_env(env)
        if args.command == "list":
            # list 只接收环境名，输出单行 JSON 对象，值为 JSON 布尔值。
            result = json.dumps(cmd_list(args.db, env), sort_keys=True)
        else:
            validate_key(args.key)
            if args.command == "set":
                value = parse_bool(args.value)
                result = cmd_set(args.db, env, args.key, value)
            elif args.command == "unset":
                result = cmd_unset(args.db, env, args.key)
            else:
                result = cmd_get(args.db, env, args.key)
    except FlagError as exc:
        sys.stderr.write(exc.code + "\n")
        return EXIT_ERROR
    sys.stdout.write(result + "\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
