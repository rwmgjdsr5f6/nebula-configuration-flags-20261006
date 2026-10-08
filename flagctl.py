#!/usr/bin/env python3
"""flagctl: 本地布尔功能开关命令行工具。

用法:
    python flagctl.py --db <数据库文件> set <环境名> <键名> <true|false> [--dry-run]
    python flagctl.py --db <数据库文件> get <环境名> <键名> [--default true|false] [--explain]
    python flagctl.py --db <数据库文件> unset <环境名> <键名> [--dry-run]
    python flagctl.py --db <数据库文件> list <环境名>
    python flagctl.py --db <数据库文件> diff <环境名左> <环境名右> [--exit-code]
    python flagctl.py --db <数据库文件> envs
    python flagctl.py --db <数据库文件> import <环境名> <JSON 文件> [--dry-run]
    python flagctl.py --db <数据库文件> export <环境名> <JSON 文件>

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
EXIT_DIFF = 1
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


def preview_set(db_path, env, key, value):
    """只读预览单个键的直接设置将带来的变化。

    返回 {键名: {"before": 原值或 None, "after": JSON 布尔值}}。存储
    分类与值校验完全复用 cmd_list 唯一一份规则：库文件缺失但父目录
    存在、有效库缺 flags 表或目标记录缺失时原值视为 None（null）；
    父目录缺失、无效库、所需列缺失或目标记录值不是严格文本
    true/false 时报 STORAGE_ERROR。其他环境及未知键记录不影响预览。
    before 与 after 相同（含 false 等于已保存的 false）时输出 {}；
    false 是有效设置，未设置不补默认值或继承值。纯只读，不创建目录、
    库文件或表，不改动任何记录。
    """
    return preview_changes(db_path, env, {key: value == "true"})


def cmd_set(db_path, env, key, value, dry_run=False):
    """保存或覆盖一个直接设置，成功时回显所写入的布尔文本。

    dry_run=True 时完全不写入，改为只读预览单键变化，输出只含该键
    的 {"before": 原值或 None, "after": 目标值}；输入校验顺序、错误
    码与存储分类与正式 set 一致，全程不创建或改动任何存储对象。
    """
    if dry_run:
        return preview_set(db_path, env, key, value)
    save_flags(db_path, env, [(key, value)])
    return value


def read_flag_with_source(db_path, env, key, default=None):
    """读取目标记录并说明值的来源，get 与 get --explain 共用的唯一规则。

    返回 (严格文本 "true"/"false", 来源)：目标记录存在合法值时来源为
    "direct"（false 也是直接设置，照常返回）；目标没有直接设置且调用方
    提供了合法默认值时来源为 "default"。存储分类、默认值兜底范围与
    cmd_get() 完全一致，只是把来源一并交给调用方决定输出形式。
    """
    try:
        with open_flag_store(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM flags WHERE env = ? AND key = ?", (env, key)
            ).fetchone()
    except FlagError as exc:
        # 默认值只兜底“没有直接设置”：库文件缺失但父目录存在、有效库
        # 缺 flags 表等 VALUE_NOT_SET 信号翻译为本次调用的默认值；
        # STORAGE_ERROR 不用默认值掩盖。
        if exc.code == "VALUE_NOT_SET" and default is not None:
            return default, "default"
        raise
    if row is None:
        if default is not None:
            return default, "default"
        raise FlagError("VALUE_NOT_SET")
    return stored_bool_text(row[0]), "direct"


def cmd_get(db_path, env, key, default=None):
    """读取目标记录的布尔值，纯只读、不修复异常数据。

    目标记录存在时返回其严格文本值（false 也是有效设置，照常返回）；
    记录存在但值不是严格的文本 true/false 时与 list/envs 走同一份校验
    （stored_bool_text()），视为存储数据损坏，报 STORAGE_ERROR：不做
    大小写转换、不去除空白、不输出原值。只检查目标记录，其他环境及
    未知键记录的异常值不在本次读取范围内。

    default 非 None（已由调用方用 parse_bool() 校验为严格文本
    "true"/"false"）时，它只作用于本次调用、不写入存储：目标没有
    直接设置（库文件缺失但父目录存在、有效库缺 flags 表或目标行不
    存在）时返回该默认值，替代原本的 VALUE_NOT_SET；父目录缺失、
    无效库、所需列缺失、连接或查询失败以及目标行值非法等
    STORAGE_ERROR 不用默认值掩盖，仍原样上报。default 为 None 时
    行为与不提供 --default 完全一致。
    """
    value, _source = read_flag_with_source(db_path, env, key, default)
    return value


def cmd_get_explained(db_path, env, key, default=None):
    """get --explain：在同一次只读读取中返回值与来源。

    校验顺序、默认值规则、存储分类与错误码与 cmd_get() 完全一致，只是
    成功时返回 {"value": JSON 布尔值, "source": "direct"/"default"}：
    目标存在合法记录（含 false、保存值等于默认值）时来源为 direct；
    目标无直接设置且提供了合法默认值时来源为 default。默认值不落库，
    读取不创建目录、库文件或表，也不修改或修复记录。
    """
    text, source = read_flag_with_source(db_path, env, key, default)
    return {"value": text == "true", "source": source}


def preview_unset(db_path, env, key):
    """只读预览撤销单个键的直接设置将带来的变化。

    返回 {键名: {"before": 当前布尔值, "after": None}}；撤销永远把整行
    删除，因此 after 固定为 null，不存在“撤销后保留 false”。原值 false
    与 true 一样是有效设置，同样输出变化，不被视为未设置；不补默认值
    或继承值。

    存储分类与值校验完全复用 cmd_get() 唯一一份的单行读取规则
    （open_flag_store() + stored_bool_text()）：库文件缺失但父目录存在、
    有效库缺 flags 表或目标行不存在时报 VALUE_NOT_SET；父目录缺失、
    无效库、所需列缺失、连接或查询失败、目标行值不是严格文本
    true/false 时报 STORAGE_ERROR。其他环境及未知键记录不在读取范围
    内，其异常值不影响预览。纯只读，不创建目录、库文件或表，不改动
    任何记录、不修复异常值。
    """
    value = cmd_get(db_path, env, key)
    return {key: {"before": value == "true", "after": None}}


def cmd_unset(db_path, env, key, dry_run=False):
    """删除一个直接设置（删除整行而非写入 false），成功时回显 unset。

    dry_run=True 时完全不删除，改为只读预览单键变化，输出只含该键的
    {"before": 当前布尔值, "after": None}：原值严格文本 true/false 时
    before 为对应 JSON 布尔值，after 为 null；目标行不存在（含库文件
    缺失、缺 flags 表）报 VALUE_NOT_SET，原值非法报 STORAGE_ERROR。
    输入校验顺序、错误码与正式 unset 一致，全程不创建或改动任何存储
    对象，也不修复异常值。
    """
    if dry_run:
        return preview_unset(db_path, env, key)
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


def preview_changes(db_path, env, data):
    """只读预览一批直接设置将带来的变化，返回
    {键名: {"before": .., "after": ..}}。

    import --dry-run 与 set --dry-run 共用这唯一一份预览规则。只比较
    data 中出现的键与目标环境的直接设置，复用 cmd_list 唯一一份存储
    分类与值校验：库文件缺失但父目录存在、有效库缺 flags 表或目标
    环境无设置时原值视为 None（null）；父目录缺失、无效库、所需列缺失
    或目标环境合法键的存储值不是严格文本 true/false 时报 STORAGE_ERROR。
    其他环境及未知键记录不影响结果。before 是原值（未设置为 None），
    after 是 data 中的目标布尔值；两者相同的键不输出。纯只读，不创建
    目录、库文件或表，不改动任何记录。
    """
    current = cmd_list(db_path, env)
    changes = {}
    for key in sorted(data):
        after = data[key]
        before = current.get(key)
        if before != after:
            changes[key] = {"before": before, "after": after}
    return changes


def cmd_import(db_path, env, file_path, dry_run=False):
    """从 JSON 文件导入一个环境的直接设置，返回 {键名: 布尔值}。

    导入覆盖目标环境中出现的同名键，保留其他环境和文件中未出现的
    记录；false 是实际设置而非删除。空对象 {} 直接成功返回，不访问
    数据库。非空导入与 set 共用唯一一份持久化规则 save_flags()：
    创建缺失的数据库文件和 flags 表，但不创建父目录；父目录不存在、
    目标不是有效数据库、flags 表缺少所需列或写入失败都报
    STORAGE_ERROR，整个导入在一个事务中完成，失败时既有记录保持
    原样。

    dry_run=True 时完全不写入：非空文件改为只读预览变化，输出只含
    变化键的 {"before": 原值或 None, "after": 导入值}，文件未提及的
    键保持原样、不表示删除；校验顺序、错误码与存储分类与普通导入
    一致，且全程不创建或改动任何存储对象。
    """
    data = load_import_file(file_path)
    if not data:
        return {}
    if dry_run:
        return preview_changes(db_path, env, data)
    save_flags(
        db_path,
        env,
        [(key, "true" if data[key] else "false") for key in sorted(data)],
    )
    return data


def cmd_export(db_path, env, file_path):
    """把一个环境已保存的直接设置导出为 JSON 文件，返回单行 JSON 文本。

    导出对象与 list 的输出一致：仅含目标环境已保存的合法键，值为 JSON
    布尔值；false 原样保留，未设置的键不出现，不补默认值或继承值。
    配置读取完全复用 cmd_list 的唯一一份存储分类与值校验规则：库文件
    缺失但父目录存在、有效库缺 flags 表或环境无合法设置时导出 {}
    （不创建数据库或表）；父目录缺失、无效库、查询所需列缺失或目标
    环境的合法键存有严格文本 true/false 之外的值时报 STORAGE_ERROR，
    此时不创建也不改动输出文件。未知键及其他环境的异常值不影响导出。

    严格按“先完整读取配置、后处理输出文件”的顺序：读取全部通过后，
    输出路径与数据库路径的规范化绝对路径相同则报 EXPORT_WRITE_ERROR；
    输出路径已存在则报 EXPORT_EXISTS 并保留原内容。写入以排他创建
    进行：父目录缺失、无法写入或写入失败都报 EXPORT_WRITE_ERROR，
    不创建目录，失败时清理本次新建的残缺文件，不遗留部分输出。
    文件为无 BOM 的 UTF-8，内容是单行 JSON 对象加换行，与 stdout
    输出的文本完全相同。全程只读源库，不改动任何记录。
    """
    data = cmd_list(db_path, env)
    text = json.dumps(data, sort_keys=True)
    if os.path.normcase(os.path.abspath(file_path)) == os.path.normcase(
        os.path.abspath(db_path)
    ):
        raise FlagError("EXPORT_WRITE_ERROR")
    if os.path.exists(file_path):
        raise FlagError("EXPORT_EXISTS")
    try:
        # 排他创建：已存在的文件不会被打开，更不会被截断或覆盖。
        fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    except FileExistsError:
        # 与上面 exists 检查之间的竞态：按同一规则报 EXPORT_EXISTS。
        raise FlagError("EXPORT_EXISTS")
    except OSError:
        raise FlagError("EXPORT_WRITE_ERROR")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((text + "\n").encode("utf-8"))
    except OSError:
        # 写入中途失败：清理由本次调用新建的文件，不遗留残缺输出。
        with contextlib.suppress(OSError):
            os.remove(file_path)
        raise FlagError("EXPORT_WRITE_ERROR")
    return text


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
    p_set.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览该键将发生的变化，不写入数据库",
    )

    p_get = sub.add_parser("get", help="读取开关值")
    p_get.add_argument("env")
    p_get.add_argument("key")
    p_get.add_argument(
        "--default",
        default=None,
        help="目标没有直接设置时本次读取使用的默认值（严格小写 true/false），"
        "不写入配置库",
    )
    p_get.add_argument(
        "--explain",
        action="store_true",
        help="输出仅含 value 和 source 的单行 JSON 对象：value 为 JSON "
        "布尔值，source 为 direct（已保存值）或 default（使用默认值）",
    )

    p_unset = sub.add_parser("unset", help="撤销开关的直接设置")
    p_unset.add_argument("env")
    p_unset.add_argument("key")
    p_unset.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览撤销该键将发生的变化，不删除记录",
    )

    p_list = sub.add_parser("list", help="列出环境已保存的开关")
    p_list.add_argument("env")

    p_diff = sub.add_parser("diff", help="只读比较两个环境的直接设置")
    p_diff.add_argument("env_left")
    p_diff.add_argument("env_right")
    p_diff.add_argument(
        "--exit-code",
        action="store_true",
        help="比较发现差异时以退出码 1 结束（无差异仍为 0，失败为 2）",
    )

    sub.add_parser("envs", help="列出库中已有直接设置的环境名")

    p_import = sub.add_parser("import", help="从 JSON 文件导入环境的直接设置")
    p_import.add_argument("env")
    p_import.add_argument("file")
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览将发生的变化，不写入数据库",
    )

    p_export = sub.add_parser("export", help="把环境的直接设置导出为 JSON 文件")
    p_export.add_argument("env")
    p_export.add_argument("file")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    exit_code = EXIT_OK
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
            diff_result = cmd_diff(args.db, left, right)
            result = json.dumps(diff_result, sort_keys=True)
            # 仅在显式要求 --exit-code 时用退出码表达比较结果：完整比较
            # 发现差异（差异对象非空）退出 1；不加该参数时即使有差异也
            # 仍退出 0。两种成功情形的 stdout/stderr 协议完全相同；任何
            # 失败仍走下方 FlagError 分支退出 2，不输出部分差异。
            if args.exit_code and diff_result:
                exit_code = EXIT_DIFF
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
                # 文件与内容校验先于访问数据库；普通导入输出仅含本次导入项
                # 的单行 JSON 对象，--dry-run 输出仅含变化键的
                # {"before": 原值或 null, "after": 导入值}，不写入存储。
                if args.dry_run:
                    # preview_changes 已按键名排序，内层固定先 before 后
                    # after；不用 sort_keys，以免调换两个字段的输出顺序。
                    # 紧凑分隔符，输出 {"key":{"before":..,"after":..}}。
                    result = json.dumps(
                        cmd_import(args.db, env, args.file, True),
                        separators=(",", ":"),
                    )
                else:
                    result = json.dumps(
                        cmd_import(args.db, env, args.file), sort_keys=True
                    )
            elif args.command == "export":
                # export 接收环境名和输出文件路径：环境名校验先于读取
                # 配置，配置完整读取后才处理输出文件；stdout 与文件内容
                # 是同一份单行 JSON 文本。
                result = cmd_export(args.db, env, args.file)
            else:
                validate_key(args.key)
                if args.command == "set":
                    value = parse_bool(args.value)
                    if args.dry_run:
                        # set --dry-run 与 import --dry-run 共用同一份预览
                        # 规则，内层固定先 before 后 after；不用 sort_keys，
                        # 以免调换两个字段的顺序。紧凑分隔符，输出
                        # {"key":{"before":..,"after":..}}。
                        result = json.dumps(
                            cmd_set(args.db, env, args.key, value, True),
                            separators=(",", ":"),
                        )
                    else:
                        result = cmd_set(args.db, env, args.key, value)
                elif args.command == "unset":
                    if args.dry_run:
                        # unset --dry-run 与 set/import --dry-run 共用同一
                        # 预览输出形式：内层固定先 before 后 after；不用
                        # sort_keys，以免调换两个字段的顺序。紧凑分隔符，
                        # 输出 {"key":{"before":..,"after":null}}。
                        result = json.dumps(
                            cmd_unset(args.db, env, args.key, True),
                            separators=(",", ":"),
                        )
                    else:
                        result = cmd_unset(args.db, env, args.key)
                else:
                    # get 在环境名、键名之后校验 --default：严格小写
                    # true/false，空串、TRUE、1、带空白等一律 INVALID_BOOL。
                    # 即使目标已有直接设置也先完成默认值校验，全部通过后
                    # 才访问存储；默认值只影响本次读取，不持久化。
                    default = None
                    if args.default is not None:
                        default = parse_bool(args.default)
                    if args.explain:
                        # --explain 复用同一次读取：成功时输出仅含 value 和
                        # source 的紧凑单行 JSON 对象，value 为 JSON 布尔值，
                        # source 区分直接设置（direct）与本次默认值（default）。
                        # 省略该参数时仍输出原有布尔文本。
                        result = json.dumps(
                            cmd_get_explained(args.db, env, args.key, default),
                            separators=(",", ":"),
                        )
                    else:
                        result = cmd_get(args.db, env, args.key, default)
    except FlagError as exc:
        sys.stderr.write(exc.code + "\n")
        return EXIT_ERROR
    sys.stdout.write(result + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
