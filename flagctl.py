#!/usr/bin/env python3
"""flagctl: 本地布尔功能开关命令行工具。

用法:
    python flagctl.py --db <数据库文件> set <环境名> <键名> <true|false>
    python flagctl.py --db <数据库文件> get <环境名> <键名>
    python flagctl.py --db <数据库文件> unset <环境名> <键名>
    python flagctl.py --db <数据库文件> list <环境名>
    python flagctl.py --db <数据库文件> diff <环境名左> <环境名右>
    python flagctl.py --db <数据库文件> envs
    python flagctl.py --db <数据库文件> import <环境名> <JSON 文件>

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


def stored_bool_text(value):
    """已保存布尔值的唯一读取校验规则，get/list/envs（及经 list 的
    diff）共用这一份。

    flags 表中合法的已保存值只有严格文本 "true" / "false"：不修剪
    空白、不转换大小写，也不接受 "yes"、"1" 等其他写法。校验通过时
    原样返回该文本（get 直接输出，list/envs 据此映射）；值非法一律
    按存储数据损坏报 STORAGE_ERROR。读取范围（目标记录、目标环境或
    全库）由各调用方通过 SQL 与 KNOWN_KEYS 过滤决定，本函数只负责
    单条值的判定，不做查询、不修复记录。
    """
    if value == "true" or value == "false":
        return value
    raise FlagError("STORAGE_ERROR")


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


def save_flags(db_path, env, items):
    """set 与非空 import 共用的直接设置持久化入口，规则只维护一份。

    items 是 (键名, 布尔文本) 对的序列，全部写入同一个环境。创建缺失
    的数据库文件和 flags 表，但不创建父目录；同名键覆盖旧值（false 是
    有效设置），其他环境及未提及的记录保持原样。整批写入在一个事务中
    完成：父目录不存在、目标不是有效数据库、flags 表缺少所需列或写入
    失败都统一报 STORAGE_ERROR，既有记录保持原样。
    """
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(SCHEMA)
            conn.executemany(
                "INSERT OR REPLACE INTO flags (env, key, value) VALUES (?, ?, ?)",
                [(env, key, value) for key, value in items],
            )
    except sqlite3.Error:
        raise FlagError("STORAGE_ERROR")
    finally:
        conn.close()


def cmd_set(db_path, env, key, value):
    save_flags(db_path, env, [(key, value)])
    return value


def cmd_get(db_path, env, key):
    """读取目标记录的布尔值，纯只读、不修复异常数据。

    目标记录不存在报 VALUE_NOT_SET；记录存在但值不是严格的文本
    true/false 时与 list/envs 走同一份校验（stored_bool_text()），
    视为存储数据损坏，报 STORAGE_ERROR：不做大小写转换、不去除空白、
    不输出原值。只检查目标记录，其他环境的异常值不在本次读取范围内。
    """
    with open_flag_store(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM flags WHERE env = ? AND key = ?", (env, key)
        ).fetchone()
    if row is None:
        raise FlagError("VALUE_NOT_SET")
    return stored_bool_text(row[0])


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

    未知键不出现在结果中；已知键的值校验完全复用唯一一份规则
    stored_bool_text()，存有 true/false 之外的值时报 STORAGE_ERROR，
    不输出部分结果。
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
        # list 只检查目标环境的已知键：未知键整行忽略，其异常值不影响
        # 结果；已知键则交给统一规则校验并映射为 JSON 布尔值。
        if key not in KNOWN_KEYS:
            continue
        result[key] = stored_bool_text(value) == "true"
    return result


def cmd_diff(db_path, left_env, right_env):
    """按输入顺序比较两个环境已知键的直接设置，返回差异对象。

    纯只读：比较逻辑直接复用 cmd_list 的存储分类与数据校验——文件
    缺失、有效库缺少 flags 表或环境无记录都视为该环境没有任何直接
    设置（空映射）；连接或查询失败、任一目标环境的已知键存有严格
    文本 true/false 之外的值都报 STORAGE_ERROR，不输出部分差异。
    其他环境及未知键的记录不参与比较，其异常值也不影响结果。

    输出只保留两侧不同的键：已设置是 JSON 布尔值，未设置是 None
    （序列化为 null），false 与未设置明确区分；两边都未设置或布尔
    值相同的键不输出。
    """
    left = cmd_list(db_path, left_env)
    right = cmd_list(db_path, right_env)
    result = {}
    for key in sorted(KNOWN_KEYS):
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value != right_value:
            result[key] = {"left": left_value, "right": right_value}
    return result


def _unique_object_pairs(pairs):
    """json.loads 的 object_pairs_hook：发现重复键即拒绝整个文件。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise FlagError("INVALID_JSON")
        result[key] = value
    return result


def _reject_constant(value):
    """json.loads 的 parse_constant：拒绝非标准常量 NaN/Infinity/-Infinity。

    Python 的 json 解析器默认接受这些 JS 风格的未加引号常量（返回
    float），但它们不属于合法 JSON；这里统一按语法错误处理，由
    load_import_file 归类为 INVALID_JSON。
    """
    raise ValueError("non-standard JSON constant: %s" % value)


def load_import_file(file_path):
    """读取并校验导入文件，返回 {键名: 布尔值}。

    校验全部在访问数据库之前完成，失败时不触碰任何存储：

    * 文件不存在或无法读取 -> IMPORT_READ_ERROR；
    * 非法 UTF-8、JSON 语法错误（含未加引号的 NaN/Infinity/
      -Infinity）、顶层不是对象、存在重复键 -> INVALID_JSON；
    * 先校验全部键名，未知键 -> UNKNOWN_KEY；
    * 再校验全部值，非 JSON 布尔值 -> INVALID_BOOL。
    """
    try:
        with open(file_path, "rb") as fh:
            raw = fh.read()
    except OSError:
        raise FlagError("IMPORT_READ_ERROR")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise FlagError("INVALID_JSON")
    try:
        data = json.loads(
            text,
            object_pairs_hook=_unique_object_pairs,
            parse_constant=_reject_constant,
        )
    except FlagError:
        raise
    except ValueError:
        raise FlagError("INVALID_JSON")
    if not isinstance(data, dict):
        raise FlagError("INVALID_JSON")
    for key in data:
        validate_key(key)
    for value in data.values():
        if not isinstance(value, bool):
            raise FlagError("INVALID_BOOL")
    return data


def cmd_import(db_path, env, file_path):
    """从 JSON 文件导入一个环境的直接设置，返回 {键名: 布尔值}。

    导入覆盖目标环境中出现的同名键，保留其他环境和文件中未出现的
    记录；false 是实际设置而非删除。空对象 {} 直接成功返回，不访问
    数据库。非空导入与 set 共用唯一一份持久化规则 save_flags()：
    创建缺失的数据库文件和 flags 表，但不创建父目录；父目录不存在、
    目标不是有效数据库、flags 表缺少所需列或写入失败都报
    STORAGE_ERROR，整个导入在一个事务中完成，失败时既有记录保持
    原样。
    """
    data = load_import_file(file_path)
    if not data:
        return {}
    save_flags(
        db_path,
        env,
        [(key, "true" if data[key] else "false") for key in sorted(data)],
    )
    return data


def cmd_envs(db_path):
    """列出库中至少有一个合法键直接设置的环境名，返回名称列表。

    纯只读查询：不创建数据库文件、目录或 flags 表，不改动任何记录。
    存储状态分类完全复用 open_flag_store 维护的唯一一份规则，把
    “没有数据”的信号（VALUE_NOT_SET：数据库文件缺失或有效库缺少
    flags 表）翻译为 envs 语义下的空结果；其余 STORAGE_ERROR 原样
    向上传递。

    只有已知键的行参与判断，未知键（含其异常值）一律忽略：只含未知
    键的环境不出现；已知键的值校验完全复用唯一一份规则
    stored_bool_text()，任一已知键存有 true/false 之外的值都视为
    存储数据损坏，报 STORAGE_ERROR，不输出部分名单。false 通过同一
    校验，因此也算已设置。同名环境去重，按名称的 Unicode 码点字典
    序（Python 默认字符串序）升序；名称按库中保存的文本原样输出，
    不去除空白、不转换大小写。
    """
    try:
        with open_flag_store(db_path) as conn:
            rows = conn.execute("SELECT env, key, value FROM flags").fetchall()
    except FlagError as exc:
        # 对 envs 而言，“没有任何已保存的值”就是空结果而非错误。
        if exc.code == "VALUE_NOT_SET":
            return []
        raise
    envs = set()
    for env, key, value in rows:
        # envs 检查全库已知键：未知键整行忽略；已知键统一校验，非法
        # 即报 STORAGE_ERROR，合法（含 false）则把环境原样计入。
        if key not in KNOWN_KEYS:
            continue
        stored_bool_text(value)
        envs.add(env)
    return sorted(envs)


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

    p_diff = sub.add_parser("diff", help="只读比较两个环境的直接设置")
    p_diff.add_argument("env_left")
    p_diff.add_argument("env_right")

    sub.add_parser("envs", help="列出库中已有直接设置的环境名")

    p_import = sub.add_parser("import", help="从 JSON 文件导入环境的直接设置")
    p_import.add_argument("env")
    p_import.add_argument("file")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "envs":
            # envs 不接收环境名或键名：只读输出环境名组成的单行 JSON 数组。
            result = json.dumps(cmd_envs(args.db), ensure_ascii=False)
        elif args.command == "diff":
            # diff 接收两个环境名，按输入顺序区分左右；比较前先校验
            # 两个环境名，任一为空或全空白都在访问存储前拒绝。
            left = normalize_env(args.env_left)
            right = normalize_env(args.env_right)
            validate_env(left)
            validate_env(right)
            result = json.dumps(cmd_diff(args.db, left, right), sort_keys=True)
        else:
            env = normalize_env(args.env)
            # 输入错误按环境名、键名、布尔值的顺序判断，
            # 且在任何校验失败前不得触碰数据库。
            validate_env(env)
            if args.command == "list":
                # list 只接收环境名，输出单行 JSON 对象，值为 JSON 布尔值。
                result = json.dumps(cmd_list(args.db, env), sort_keys=True)
            elif args.command == "import":
                # import 接收环境名和 JSON 文件路径：环境名校验先于读文件，
                # 文件与内容校验先于访问数据库；输出仅含本次导入项的
                # 单行 JSON 对象。
                result = json.dumps(
                    cmd_import(args.db, env, args.file), sort_keys=True
                )
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
