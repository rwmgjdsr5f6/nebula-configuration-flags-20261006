#!/usr/bin/env python3
"""flagctl.py 的命令行回归测试。

覆盖本地布尔开关 set/get 在进程间的持久化行为，以及 set 的输入校验：

* 合法设置跨进程持久化、覆盖写、环境之间互不影响；
* 环境名两端空白按去除空白后的名称落库；
* 读取尚未创建的数据库得到 VALUE_NOT_SET 且不创建文件；
* get 在四种数据库状态下的行为：有效库缺 flags 表、flags 表无对应
  环境记录（VALUE_NOT_SET），父目录不存在、目标为普通文本文件
  （STORAGE_ERROR），且读取前后相关数据与文件状态保持不变；
* get 对存储值的严格校验：固定样例 dev/new_ui=yes 报 STORAGE_ERROR
  而 qa/new_ui=false 正常输出 false，yes/TRUE/1/空串/带空白等非法
  文本逐一报 STORAGE_ERROR 且原值原样保留，合法 true/false 正常读取，
  只检查目标记录（其他环境的异常值不影响合法目标），flags 表缺少
  查询所需的 value 列时报 STORAGE_ERROR；
* unset 撤销已保存记录（原值 true 与 false 各一条固定样例）的输出
  协议：记录物理消失而非改成 false、环境名两端空白命中同一记录、
  重复撤销报 VALUE_NOT_SET 且不影响其他环境；
* unset 的 EMPTY_ENV / UNKNOWN_KEY 拒绝顺序：校验失败不创建数据库、
  不改变既有记录；
* unset 在五种数据库状态下的行为：文件缺失、有效库缺 flags 表、表内
  无目标记录（VALUE_NOT_SET），父目录不存在、目标为普通文本文件
  （STORAGE_ERROR），且调用前后相关数据与文件状态保持不变；
* get 的 EMPTY_ENV / UNKNOWN_KEY 拒绝顺序：键名不去空白、不转换大小写，
  校验失败不创建数据库文件或目录、不新增表或记录、不改变既有记录，
  父目录不存在时错误优先级不变（不降级为 STORAGE_ERROR）；
* EMPTY_ENV / UNKNOWN_KEY / INVALID_BOOL 的拒绝顺序与输出协议；
* set 在三种存储失败状态下的行为：父目录不存在、目标为普通文本
  文件、有效库中 flags 表缺少 value 列（STORAGE_ERROR），且调用
  前后目录、文件字节、表结构与既有记录全部保持不变；
* set 的输入校验先于存储访问：父目录不存在时 EMPTY_ENV /
  UNKNOWN_KEY / INVALID_BOOL 依旧按原优先级报告，不降级为
  STORAGE_ERROR，且不创建目录或文件；
* diff 只读比较两个环境的直接设置：固定样例 dev/new_ui=false、
  qa/new_ui=true 按输入顺序输出左右差异，交换顺序左右互换；相同
  布尔值、两侧都未设置与同一环境自比都返回 {}；一侧未设置对应值
  为 null 且已保存的 false 不被当成未设置；环境名两端空白被去除、
  大小写敏感；未知键与其他环境的异常值不参与比较；空或全空白环境
  名报 EMPTY_ENV 且优先于存储访问；文件缺失或有效库缺 flags 表
  返回 {} 且不创建文件或表；父目录不存在、目标为普通文本文件、
  flags 表缺 value 列或任一目标环境存有非法值时报 STORAGE_ERROR，
  不输出部分差异，且调用前后目录、文件字节、表结构与记录保持不变。
* import 从 JSON 文件导入一个环境的直接设置：固定样例
  dev/new_ui=true、qa/new_ui=true 导入 {"new_ui": false} 后 dev
  变为 false、qa 保持 true；空对象 {} 不访问数据库；非空导入在
  全新路径上创建数据库文件和 flags 表；环境名 -> 读文件 -> JSON
  解析 -> 键名 -> 布尔值的校验顺序（EMPTY_ENV / IMPORT_READ_ERROR
  / INVALID_JSON / UNKNOWN_KEY / INVALID_BOOL），校验失败不创建
  库或表、不改动记录；父目录不存在、普通文本文件、flags 表缺列
  报 STORAGE_ERROR 且既有记录保持原样。

所有业务调用均以子进程执行 ``python flagctl.py --db <临时数据库>``，
每个用例使用独立的临时目录，结束后自动清理，只依赖 Python 3 标准库。
可在项目目录直接运行::

    python -m unittest discover
    python -m unittest test_flagctl.TestPersistence.test_set_true_persists_across_processes
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FLAGCTL = os.path.join(HERE, "flagctl.py")


class FlagctlCliTestCase(unittest.TestCase):
    """公共夹具：每个用例独享一个临时目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def db_path(self, label):
        """返回临时目录中一个（尚不存在的）数据库路径。"""
        return os.path.join(self.tmpdir, label + ".sqlite")

    def run_flagctl(self, db, *args):
        """在全新进程中执行 flagctl.py，返回完成后的进程对象。"""
        return subprocess.run(
            [sys.executable, FLAGCTL, "--db", db, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def set_flag(self, db, env, key, value):
        return self.run_flagctl(db, "set", env, key, value)

    def get_flag(self, db, env, key):
        return self.run_flagctl(db, "get", env, key)

    def unset_flag(self, db, env, key):
        return self.run_flagctl(db, "unset", env, key)

    def list_flags(self, db, env):
        return self.run_flagctl(db, "list", env)

    def diff_flags(self, db, left, right):
        return self.run_flagctl(db, "diff", left, right)

    def envs_flags(self, db):
        return self.run_flagctl(db, "envs")

    def import_flags(self, db, env, file_path):
        return self.run_flagctl(db, "import", env, file_path)

    def write_import_file(self, content, name="settings.json", raw=False):
        """在临时目录写入导入文件并返回路径；raw=True 时 content 为字节。"""
        path = os.path.join(self.tmpdir, name)
        mode = "wb" if raw else "w"
        with open(path, mode, **({} if raw else {"encoding": "utf-8"})) as fh:
            fh.write(content)
        return path

    def read_table_names(self, db):
        """返回数据库中全部用户表名（升序）。"""
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        finally:
            conn.close()
        return [row[0] for row in rows]

    def read_flags_rows(self, db):
        """返回 flags 表全部 (env, key, value) 行（按主键升序）。"""
        conn = sqlite3.connect(db)
        try:
            return conn.execute(
                "SELECT env, key, value FROM flags ORDER BY env, key"
            ).fetchall()
        finally:
            conn.close()

    def read_flags_columns(self, db):
        """返回 flags 表的列名（按定义顺序）。"""
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute("PRAGMA table_info(flags)").fetchall()
        finally:
            conn.close()
        return [row[1] for row in rows]

    def read_flags_env_key_rows(self, db):
        """返回 flags 表全部 (env, key) 行（按主键升序）。"""
        conn = sqlite3.connect(db)
        try:
            return conn.execute(
                "SELECT env, key FROM flags ORDER BY env, key"
            ).fetchall()
        finally:
            conn.close()

    def assertCommandOk(self, proc, output):
        """退出 0、stdout 严格为 output 加换行、stderr 为空。"""
        self.assertEqual(
            proc.returncode, 0, "期望退出码 0，实际 stderr: %r" % proc.stderr
        )
        self.assertEqual(proc.stdout, output + "\n")
        self.assertEqual(proc.stderr, "")

    def assertCommandError(self, proc, code):
        """退出 2、stdout 为空、stderr 严格为错误码加换行。"""
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, code + "\n")

    def assert_rejected_on_fresh_path(self, label, env, key, value, code):
        """全新路径上拒绝写入：报错且不留下数据库文件。"""
        db = self.db_path(label)
        self.assertFalse(os.path.exists(db))
        proc = self.set_flag(db, env, key, value)
        self.assertCommandError(proc, code)
        self.assertFalse(os.path.exists(db), "校验失败不得创建数据库文件: %s" % db)

    def assert_rejected_keeps_saved_false(self, label, env, key, value, code):
        """已保存 false 的库上拒绝写入：报错且既有值保持 false。"""
        db = self.db_path(label)
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        proc = self.set_flag(db, env, key, value)
        self.assertCommandError(proc, code)
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")


class TestPersistence(FlagctlCliTestCase):
    """合法设置的持久化与读取协议。"""

    def test_set_true_persists_across_processes(self):
        # 首次设置 dev/new_ui=true；写入与读取分别在独立进程中完成。
        db = self.db_path("set_true")
        written = self.set_flag(db, "dev", "new_ui", "true")
        self.assertCommandOk(written, "true")
        self.assertTrue(os.path.isfile(db))

        read = self.get_flag(db, "dev", "new_ui")
        self.assertCommandOk(read, "true")

    def test_overwrite_true_with_false(self):
        # 同一环境同一键改为 false 后，新进程读到的是最新值。
        db = self.db_path("overwrite")
        self.assertCommandOk(
            self.set_flag(db, "dev", "new_ui", "true"), "true"
        )
        self.assertCommandOk(
            self.set_flag(db, "dev", "new_ui", "false"), "false"
        )
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")

    def test_distinct_envs_do_not_overwrite(self):
        # dev=false 与 qa=true 互不覆盖。
        db = self.db_path("envs")
        self.assertCommandOk(
            self.set_flag(db, "dev", "new_ui", "false"), "false"
        )
        self.assertCommandOk(
            self.set_flag(db, "qa", "new_ui", "true"), "true"
        )
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_env_surrounding_whitespace_matches_stripped(self):
        # 带两端空白的环境名与去除空白后的 dev 对应同一条记录。
        db = self.db_path("whitespace")
        self.assertCommandOk(
            self.set_flag(db, " dev ", "new_ui", "true"), "true"
        )
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")

        # 用不带空白的名字覆盖写，带空白读取仍命中同一记录。
        self.assertCommandOk(
            self.set_flag(db, "dev", "new_ui", "false"), "false"
        )
        self.assertCommandOk(self.get_flag(db, "  dev  ", "new_ui"), "false")

    def test_get_missing_db_reports_value_not_set(self):
        # 父目录存在但数据库文件尚未创建：退出 2、VALUE_NOT_SET、文件仍不存在。
        db = self.db_path("missing")
        self.assertTrue(os.path.isdir(self.tmpdir))
        self.assertFalse(os.path.exists(db))

        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "VALUE_NOT_SET")
        self.assertFalse(os.path.exists(db), "读取缺失数据库不得创建文件: %s" % db)


class TestSetValidation(FlagctlCliTestCase):
    """set 的输入校验：每种错误都在全新路径和已存 false 的库上各验证一次。"""

    def test_empty_env_rejected(self):
        self.assert_rejected_on_fresh_path(
            "fresh_empty_env", "", "new_ui", "true", "EMPTY_ENV"
        )
        self.assert_rejected_keeps_saved_false(
            "kept_empty_env", "", "new_ui", "true", "EMPTY_ENV"
        )

    def test_whitespace_only_env_rejected(self):
        self.assert_rejected_on_fresh_path(
            "fresh_blank_env", "   ", "new_ui", "true", "EMPTY_ENV"
        )
        self.assert_rejected_keeps_saved_false(
            "kept_blank_env", "   ", "new_ui", "true", "EMPTY_ENV"
        )

    def test_unknown_key_rejected(self):
        self.assert_rejected_on_fresh_path(
            "fresh_unknown_key", "dev", "other_key", "true", "UNKNOWN_KEY"
        )
        self.assert_rejected_keeps_saved_false(
            "kept_unknown_key", "dev", "other_key", "true", "UNKNOWN_KEY"
        )

    def test_uppercase_bool_rejected(self):
        self.assert_rejected_on_fresh_path(
            "fresh_upper_bool", "dev", "new_ui", "TRUE", "INVALID_BOOL"
        )
        self.assert_rejected_keeps_saved_false(
            "kept_upper_bool", "dev", "new_ui", "TRUE", "INVALID_BOOL"
        )

    def test_numeric_bool_rejected(self):
        self.assert_rejected_on_fresh_path(
            "fresh_one_bool", "dev", "new_ui", "1", "INVALID_BOOL"
        )
        self.assert_rejected_keeps_saved_false(
            "kept_one_bool", "dev", "new_ui", "1", "INVALID_BOOL"
        )

    def test_empty_bool_rejected(self):
        self.assert_rejected_on_fresh_path(
            "fresh_empty_bool", "dev", "new_ui", "", "INVALID_BOOL"
        )
        self.assert_rejected_keeps_saved_false(
            "kept_empty_bool", "dev", "new_ui", "", "INVALID_BOOL"
        )

    def test_empty_env_takes_precedence_over_key_and_bool(self):
        # 环境、键、值同时不合法：先报告 EMPTY_ENV。
        self.assert_rejected_on_fresh_path(
            "fresh_order_env", "", "bad_key", "TRUE", "EMPTY_ENV"
        )
        self.assert_rejected_keeps_saved_false(
            "kept_order_env", "", "bad_key", "TRUE", "EMPTY_ENV"
        )

    def test_unknown_key_takes_precedence_over_bool(self):
        # 环境合法而键、值均不合法：报告 UNKNOWN_KEY。
        self.assert_rejected_on_fresh_path(
            "fresh_order_key", "dev", "bad_key", "TRUE", "UNKNOWN_KEY"
        )
        self.assert_rejected_keeps_saved_false(
            "kept_order_key", "dev", "bad_key", "TRUE", "UNKNOWN_KEY"
        )


class TestGetValidation(FlagctlCliTestCase):
    """get 的输入校验：拒绝发生在触碰数据库之前。

    每种非法输入都在三种数据库路径上各验证一次：

    * 父目录存在但数据库文件尚未创建——报错后文件仍不存在；
    * 已保存 dev/new_ui=false 与 qa/new_ui=true 的库——报错后全部既有
      记录和值保持不变，不新增表或记录，随后正常读取仍分别返回
      false 和 true；
    * 父目录不存在——错误优先级不变（不变成 STORAGE_ERROR），且不创建
      缺失的目录或数据库文件。
    """

    def assert_get_rejected_on_fresh_path(self, label, env, key, code):
        """父目录存在但数据库文件尚未创建：报错且不留下数据库文件。"""
        db = self.db_path(label)
        self.assertTrue(os.path.isdir(self.tmpdir))
        self.assertFalse(os.path.exists(db))
        with self.subTest(env=env, key=key, db=db):
            proc = self.get_flag(db, env, key)
            self.assertCommandError(proc, code)
            self.assertFalse(
                os.path.exists(db), "校验失败不得创建数据库文件: %s" % db
            )

    def assert_get_rejected_keeps_saved_rows(self, label, env, key, code):
        """已有记录的库上拒绝读取：报错且既有记录全部保持原样。"""
        db = self.db_path(label)
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        with self.subTest(env=env, key=key, db=db):
            proc = self.get_flag(db, env, key)
            self.assertCommandError(proc, code)
            self.assertEqual(self.read_table_names(db), ["flags"])
            self.assertEqual(
                self.read_flags_rows(db),
                [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
            )
            self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")
            self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def assert_get_rejected_on_missing_parent(self, label, env, key, code):
        """父目录不存在：错误优先级不变，且不创建目录或文件。"""
        missing_dir = os.path.join(self.tmpdir, label + "_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        with self.subTest(env=env, key=key, db=db):
            proc = self.get_flag(db, env, key)
            self.assertCommandError(proc, code)
            self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
            self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def check_get_rejected_everywhere(self, label, env, key, code):
        """在三种数据库路径上验证同一非法输入得到同一拒绝结果。"""
        self.assert_get_rejected_on_fresh_path(label + "_fresh", env, key, code)
        self.assert_get_rejected_keeps_saved_rows(label + "_kept", env, key, code)
        self.assert_get_rejected_on_missing_parent(
            label + "_no_parent", env, key, code
        )

    def test_empty_env_rejected(self):
        self.check_get_rejected_everywhere(
            "get_empty_env", "", "new_ui", "EMPTY_ENV"
        )

    def test_whitespace_only_env_rejected(self):
        self.check_get_rejected_everywhere(
            "get_blank_env", "   ", "new_ui", "EMPTY_ENV"
        )

    def test_unknown_key_rejected(self):
        self.check_get_rejected_everywhere(
            "get_unknown_key", "dev", "other_key", "UNKNOWN_KEY"
        )

    def test_key_with_surrounding_whitespace_rejected(self):
        # 键名不做去空白处理：带两端空格的 new_ui 不是已知键。
        self.check_get_rejected_everywhere(
            "get_padded_key", "dev", " new_ui ", "UNKNOWN_KEY"
        )

    def test_key_case_mismatch_rejected(self):
        # 键名不做大小写转换：New_UI 不是已知键。
        self.check_get_rejected_everywhere(
            "get_case_key", "dev", "New_UI", "UNKNOWN_KEY"
        )

    def test_empty_env_takes_precedence_over_unknown_key(self):
        # 环境名与键同时不合法：唯一结果是 EMPTY_ENV。
        self.check_get_rejected_everywhere(
            "get_order_env_first", "", "bad_key", "EMPTY_ENV"
        )


class TestGetStorageStates(FlagctlCliTestCase):
    """get 在四种数据库状态下的退出码、输出协议与现场保持。

    统一读取 dev/new_ui：前两种状态（有效库缺 flags 表、flags 表只有
    其他环境的记录）报 VALUE_NOT_SET；后两种（父目录不存在、目标为普通
    文本文件）报 STORAGE_ERROR。每个用例同时核对读取前后的数据或文件。
    """

    def test_valid_db_without_flags_table_reports_value_not_set(self):
        # 有效 SQLite 库只含无关表：报 VALUE_NOT_SET，且不新增 flags 表、
        # 原有表与数据保持不变。
        db = self.db_path("no_flags_table")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO notes (text) VALUES ('sample note')")
        finally:
            conn.close()
        self.assertEqual(self.read_table_names(db), ["notes"])

        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "VALUE_NOT_SET")

        self.assertEqual(self.read_table_names(db), ["notes"])
        conn = sqlite3.connect(db)
        try:
            notes = conn.execute("SELECT id, text FROM notes").fetchall()
        finally:
            conn.close()
        self.assertEqual(notes, [(1, "sample note")])

    def test_flags_table_without_dev_row_reports_value_not_set(self):
        # flags 表只有 qa/new_ui=false：读 dev 报 VALUE_NOT_SET，
        # qa 记录保持 false，且不新增 dev 记录。
        db = self.db_path("only_qa_row")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertEqual(
            self.read_flags_rows(db), [("qa", "new_ui", "false")]
        )

        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "VALUE_NOT_SET")

        self.assertEqual(
            self.read_flags_rows(db), [("qa", "new_ui", "false")]
        )
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_missing_parent_directory_reports_storage_error(self):
        # 父目录不存在：报 STORAGE_ERROR，且不创建目录或文件。
        missing_dir = os.path.join(self.tmpdir, "no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_plain_text_file_reports_storage_error(self):
        # 目标是已存在的普通文本文件而非 SQLite 库：报 STORAGE_ERROR，
        # 原文件内容保持原样。
        db = self.db_path("plain_text")
        content = "this is not a sqlite database\njust fictional config\n"
        with open(db, "w", encoding="utf-8") as fh:
            fh.write(content)

        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")

        with open(db, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), content)


class TestGetStoredValueValidation(FlagctlCliTestCase):
    """get 只接受严格文本 true/false 的回归验证。

    记录存在但值不是严格的 "true"/"false" 文本时，get 必须报
    STORAGE_ERROR（退出 2、stdout 为空、stderr 严格为
    ``STORAGE_ERROR\\n``），不得通过大小写转换、数字转换或去除空白
    接受，也不得输出原值；读取前后记录逐行保持不变。
    """

    def seed_flags_table(self, db, rows):
        """直接建表并写入 (env, key, value) 行，绕过 set 的输入校验。"""
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.executemany(
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)",
                    rows,
                )
        finally:
            conn.close()

    def test_fixed_samples_yes_errors_false_ok_and_rows_unchanged(self):
        # 固定样例：dev/new_ui=yes、qa/new_ui=false。读 dev 报
        # STORAGE_ERROR，读 qa 正常输出 false；两次查询前后两条记录
        # （含非法原值 yes）逐字节保持相同。
        db = self.db_path("get_fixed_bad_value")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")],
        )
        before = self.read_flags_rows(db)
        self.assertEqual(
            before, [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")]
        )

        bad = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(bad, "STORAGE_ERROR")
        self.assertEqual(self.read_flags_rows(db), before)

        good = self.get_flag(db, "qa", "new_ui")
        self.assertCommandOk(good, "false")
        self.assertEqual(self.read_flags_rows(db), before)

    def test_each_invalid_stored_text_reports_storage_error(self):
        # yes、TRUE、1、空字符串、带空格的 true 均为非法存储值：
        # 逐一报 STORAGE_ERROR，原值原样保留，不做任何转换或去空白。
        invalid_values = ["yes", "TRUE", "1", "", " true", "true ", " true ", "True"]
        for index, value in enumerate(invalid_values):
            with self.subTest(value=value):
                db = self.db_path("get_bad_value_%d" % index)
                self.seed_flags_table(db, [("dev", "new_ui", value)])

                proc = self.get_flag(db, "dev", "new_ui")
                self.assertCommandError(proc, "STORAGE_ERROR")

                # 读取不修复数据：原值（含空串与空白）原样保留。
                self.assertEqual(
                    self.read_flags_rows(db), [("dev", "new_ui", value)]
                )

    def test_valid_true_and_false_read_back_verbatim(self):
        # 合法存储值严格输出原值：true 输出 true、false 输出 false。
        db = self.db_path("get_valid_values")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "true"), ("qa", "new_ui", "false")],
        )

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "false")],
        )

    def test_invalid_value_in_other_env_does_not_affect_target(self):
        # 读取只检查目标记录：qa 下的非法值不影响 dev 的合法读取，
        # 读 dev 成功后 qa 的非法原值仍原样保留。
        db = self.db_path("get_other_env_bad")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "true"), ("qa", "new_ui", "yes")],
        )

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "yes")],
        )

        # 直接读 qa 的非法记录仍报 STORAGE_ERROR。
        self.assertCommandError(self.get_flag(db, "qa", "new_ui"), "STORAGE_ERROR")

    def test_missing_target_row_still_reports_value_not_set(self):
        # 其他键/环境的记录（含异常值）不影响目标记录缺失的判定：
        # dev/new_ui 不存在仍报 VALUE_NOT_SET。
        db = self.db_path("get_target_missing")
        self.seed_flags_table(
            db,
            [("qa", "new_ui", "false"), ("dev", "other_key", "true")],
        )

        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "VALUE_NOT_SET")

        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "other_key", "true"), ("qa", "new_ui", "false")],
        )

    def test_flags_table_without_value_column_reports_storage_error(self):
        # flags 表缺少查询所需的 value 列：报 STORAGE_ERROR，表结构与
        # 既有记录保持不变。
        db = self.db_path("get_no_value_column")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key) VALUES (?, ?)", ("dev", "new_ui")
                )
        finally:
            conn.close()
        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("dev", "new_ui")])

        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")

        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("dev", "new_ui")])


class TestUnsetPersistence(FlagctlCliTestCase):
    """unset 撤销记录的输出协议与物理删除验证。

    固定使用 dev/new_ui 与 qa/new_ui 两个样例：dev 记录被撤销后必须
    物理消失（get 得到 VALUE_NOT_SET），而不是被改成 false 后保留；
    qa 记录始终不受影响。
    """

    def test_unset_removes_record_stored_as_true(self):
        # dev/new_ui=true 与 qa/new_ui=false 并存：撤销 dev 后退出 0、
        # stdout 严格为 "unset\\n"、stderr 为空；dev 记录物理消失，
        # qa 保持 false。
        db = self.db_path("unset_true")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")

        removed = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandOk(removed, "unset")

        self.assertEqual(
            self.read_flags_rows(db), [("qa", "new_ui", "false")]
        )
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_unset_removes_record_stored_as_false(self):
        # 原值为 false 时同样是删除整行，而不是保留一行 false。
        db = self.db_path("unset_false")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")

        removed = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandOk(removed, "unset")

        self.assertEqual(
            self.read_flags_rows(db), [("qa", "new_ui", "true")]
        )
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_unset_with_surrounding_whitespace_matches_same_record(self):
        # 带两端空白的环境名必须命中去除空白后的同一条 dev 记录。
        db = self.db_path("unset_whitespace")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")

        removed = self.unset_flag(db, "  dev  ", "new_ui")
        self.assertCommandOk(removed, "unset")

        self.assertEqual(
            self.read_flags_rows(db), [("qa", "new_ui", "false")]
        )
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_unset_already_removed_row_reports_value_not_set(self):
        # 已撤销的记录再次 unset：报 VALUE_NOT_SET，不重新创建记录，
        # 不把原值改成 false 后保留，也不影响 qa。
        db = self.db_path("unset_twice")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        self.assertCommandOk(self.unset_flag(db, "dev", "new_ui"), "unset")

        again = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandError(again, "VALUE_NOT_SET")

        self.assertEqual(
            self.read_flags_rows(db), [("qa", "new_ui", "true")]
        )
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")


class TestUnsetValidation(FlagctlCliTestCase):
    """unset 的输入校验：拒绝发生在触碰数据库之前。"""

    def assert_unset_rejected_on_fresh_path(self, label, env, key, code):
        """全新路径上拒绝撤销：报错且不留下数据库文件。"""
        db = self.db_path(label)
        self.assertFalse(os.path.exists(db))
        proc = self.unset_flag(db, env, key)
        self.assertCommandError(proc, code)
        self.assertFalse(os.path.exists(db), "校验失败不得创建数据库文件: %s" % db)

    def assert_unset_rejected_keeps_saved_rows(self, label, env, key, code):
        """已有记录的库上拒绝撤销：报错且既有记录全部保持原样。"""
        db = self.db_path(label)
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        proc = self.unset_flag(db, env, key)
        self.assertCommandError(proc, code)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_empty_env_rejected(self):
        self.assert_unset_rejected_on_fresh_path(
            "unset_fresh_empty_env", "", "new_ui", "EMPTY_ENV"
        )
        self.assert_unset_rejected_keeps_saved_rows(
            "unset_kept_empty_env", "", "new_ui", "EMPTY_ENV"
        )

    def test_whitespace_only_env_rejected(self):
        self.assert_unset_rejected_on_fresh_path(
            "unset_fresh_blank_env", "   ", "new_ui", "EMPTY_ENV"
        )
        self.assert_unset_rejected_keeps_saved_rows(
            "unset_kept_blank_env", "   ", "new_ui", "EMPTY_ENV"
        )

    def test_unknown_key_rejected(self):
        self.assert_unset_rejected_on_fresh_path(
            "unset_fresh_unknown_key", "dev", "other_key", "UNKNOWN_KEY"
        )
        self.assert_unset_rejected_keeps_saved_rows(
            "unset_kept_unknown_key", "dev", "other_key", "UNKNOWN_KEY"
        )

    def test_empty_env_takes_precedence_over_unknown_key(self):
        # 环境名与键同时不合法：先报告 EMPTY_ENV。
        self.assert_unset_rejected_on_fresh_path(
            "unset_fresh_order", "", "bad_key", "EMPTY_ENV"
        )
        self.assert_unset_rejected_keeps_saved_rows(
            "unset_kept_order", "", "bad_key", "EMPTY_ENV"
        )


class TestUnsetStorageStates(FlagctlCliTestCase):
    """unset 在五种数据库状态下的退出码、输出协议与现场保持。

    前三种状态（文件缺失、有效库缺 flags 表、flags 表只有其他环境的
    记录）报 VALUE_NOT_SET；后两种（父目录不存在、目标为普通文本文
    件）报 STORAGE_ERROR。每个用例同时核对调用前后的文件或数据。
    """

    def test_missing_db_reports_value_not_set(self):
        # 父目录存在但数据库文件不存在：报 VALUE_NOT_SET，且不创建文件。
        db = self.db_path("unset_missing")
        self.assertTrue(os.path.isdir(self.tmpdir))
        self.assertFalse(os.path.exists(db))

        proc = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "VALUE_NOT_SET")
        self.assertFalse(os.path.exists(db), "撤销缺失数据库不得创建文件: %s" % db)

    def test_valid_db_without_flags_table_reports_value_not_set(self):
        # 有效 SQLite 库只含无关表：报 VALUE_NOT_SET，不补建 flags 表，
        # 原有表与数据保持不变。
        db = self.db_path("unset_no_flags_table")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO notes (text) VALUES ('sample note')")
        finally:
            conn.close()
        self.assertEqual(self.read_table_names(db), ["notes"])

        proc = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "VALUE_NOT_SET")

        self.assertEqual(self.read_table_names(db), ["notes"])
        conn = sqlite3.connect(db)
        try:
            notes = conn.execute("SELECT id, text FROM notes").fetchall()
        finally:
            conn.close()
        self.assertEqual(notes, [(1, "sample note")])

    def test_flags_table_without_dev_row_reports_value_not_set(self):
        # flags 表只有 qa/new_ui=false：撤销 dev 报 VALUE_NOT_SET，
        # qa 记录保持 false，且不新增 dev 记录。
        db = self.db_path("unset_only_qa_row")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertEqual(
            self.read_flags_rows(db), [("qa", "new_ui", "false")]
        )

        proc = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "VALUE_NOT_SET")

        self.assertEqual(
            self.read_flags_rows(db), [("qa", "new_ui", "false")]
        )
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_missing_parent_directory_reports_storage_error(self):
        # 父目录不存在：报 STORAGE_ERROR，且不创建目录或文件。
        missing_dir = os.path.join(self.tmpdir, "no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        proc = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_plain_text_file_reports_storage_error(self):
        # 目标是已存在的普通文本文件而非 SQLite 库：报 STORAGE_ERROR，
        # 原文件内容保持原样。
        db = self.db_path("unset_plain_text")
        content = "this is not a sqlite database\njust fictional config\n"
        with open(db, "w", encoding="utf-8") as fh:
            fh.write(content)

        proc = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")

        with open(db, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), content)


class TestSetStorageStates(FlagctlCliTestCase):
    """set 写入失败时的退出码、输出协议与现场保持。

    统一执行合法的 ``set dev new_ui true``，在三种固定存储状态下都
    必须退出 2、stdout 为空、stderr 严格为 ``STORAGE_ERROR\\n``，
    不得输出成功值或异常堆栈，且调用前后的目录、文件与数据保持
    不变。
    """

    def test_missing_parent_directory_reports_storage_error(self):
        # 父目录不存在：报 STORAGE_ERROR，且不创建目录或数据库文件。
        missing_dir = os.path.join(self.tmpdir, "set_no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        proc = self.set_flag(db, "dev", "new_ui", "true")
        self.assertCommandError(proc, "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_plain_text_file_reports_storage_error(self):
        # 目标是固定内容的普通文本文件而非 SQLite 库：报 STORAGE_ERROR，
        # 文件逐字节保持原样。
        db = self.db_path("set_plain_text")
        content = b"this is not a sqlite database\njust fictional config\n"
        with open(db, "wb") as fh:
            fh.write(content)

        proc = self.set_flag(db, "dev", "new_ui", "true")
        self.assertCommandError(proc, "STORAGE_ERROR")

        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), content)

    def test_flags_table_without_value_column_reports_storage_error(self):
        # 有效 SQLite 库，flags 表只有 env 和 key 两列并预存 qa/new_ui：
        # 报 STORAGE_ERROR，表结构与 qa 记录保持不变，不新增 dev 记录。
        db = self.db_path("set_no_value_column")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key) VALUES (?, ?)", ("qa", "new_ui")
                )
        finally:
            conn.close()
        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("qa", "new_ui")])

        proc = self.set_flag(db, "dev", "new_ui", "true")
        self.assertCommandError(proc, "STORAGE_ERROR")

        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("qa", "new_ui")])


class TestSetValidationBeforeStorage(FlagctlCliTestCase):
    """set 的输入校验先于存储访问。

    在父目录不存在的同一路径上验证：非法输入依旧按原有优先级报告
    EMPTY_ENV / UNKNOWN_KEY / INVALID_BOOL，不降级为 STORAGE_ERROR，
    且不创建缺失的目录或数据库文件。
    """

    def assert_set_rejected_on_missing_parent(self, label, env, key, value, code):
        """父目录不存在：校验错误优先于 STORAGE_ERROR，不创建目录或文件。"""
        missing_dir = os.path.join(self.tmpdir, label + "_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        proc = self.set_flag(db, env, key, value)
        self.assertCommandError(proc, code)
        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_empty_env_takes_precedence_over_key_and_bool(self):
        # 环境名仅为空格，键和值同时非法：唯一错误为 EMPTY_ENV。
        self.assert_set_rejected_on_missing_parent(
            "set_order_env_first", "   ", "bad_key", "TRUE", "EMPTY_ENV"
        )

    def test_unknown_key_takes_precedence_over_bool(self):
        # 环境合法而键、值均不合法：唯一错误为 UNKNOWN_KEY。
        self.assert_set_rejected_on_missing_parent(
            "set_order_key_first", "dev", "other_key", "TRUE", "UNKNOWN_KEY"
        )

    def test_invalid_bool_rejected_before_storage(self):
        # 环境与键合法、值非法：唯一错误为 INVALID_BOOL。
        self.assert_set_rejected_on_missing_parent(
            "set_invalid_bool", "dev", "new_ui", "TRUE", "INVALID_BOOL"
        )


class TestList(FlagctlCliTestCase):
    """list 按环境列出已保存的已知键直接设置。

    输出协议：退出 0、stderr 为空、stdout 为单行 JSON 对象加换行，
    对象的键是开关名、值是 JSON 布尔值；只包含目标环境已保存且属于
    已知键集合的记录，未设置的键不补 false，已保存的 false 不省略。
    查询不创建数据库文件、目录或表，也不改动已有记录。
    """

    def assertListOk(self, proc, expected):
        """退出 0、stderr 为空、stdout 为单行 JSON 且解析结果等于 expected。"""
        self.assertEqual(
            proc.returncode, 0, "期望退出码 0，实际 stderr: %r" % proc.stderr
        )
        self.assertEqual(proc.stderr, "")
        self.assertTrue(proc.stdout.endswith("\n"), "输出必须以换行结束")
        body = proc.stdout[:-1]
        self.assertNotIn("\n", body, "JSON 输出必须只有一行")
        self.assertEqual(json.loads(body), expected)

    def test_list_returns_saved_values_per_env(self):
        # dev/new_ui=false、qa/new_ui=true：各环境只看到自己的记录，
        # 已保存的 false 出现在结果中且是 JSON 布尔值而非字符串。
        db = self.db_path("list_envs")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")

        self.assertListOk(self.list_flags(db, "dev"), {"new_ui": False})
        self.assertListOk(self.list_flags(db, "qa"), {"new_ui": True})

        # 查询不改库：既有记录保持原样。
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )

    def test_list_env_surrounding_whitespace_matches_stripped(self):
        # 带两端空白的 dev 与 dev 返回同一结果。
        db = self.db_path("list_whitespace")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")

        self.assertListOk(self.list_flags(db, "  dev  "), {"new_ui": True})

    def test_list_env_without_rows_returns_empty_object(self):
        # 只有 qa 记录的库查询 dev：输出 {}，不补 false，不新增记录。
        db = self.db_path("list_only_qa")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")

        self.assertListOk(self.list_flags(db, "dev"), {})

        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "true")])

    def test_list_missing_db_returns_empty_object_without_creating_file(self):
        # 父目录存在但数据库文件缺失：输出 {}，文件仍不存在。
        db = self.db_path("list_missing")
        self.assertTrue(os.path.isdir(self.tmpdir))
        self.assertFalse(os.path.exists(db))

        self.assertListOk(self.list_flags(db, "dev"), {})

        self.assertFalse(os.path.exists(db), "list 不得创建数据库文件: %s" % db)

    def test_list_valid_db_without_flags_table_returns_empty_object(self):
        # 有效库缺少 flags 表：输出 {}，不补建表，原有表与数据不变。
        db = self.db_path("list_no_flags_table")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO notes (text) VALUES ('sample note')")
        finally:
            conn.close()

        self.assertListOk(self.list_flags(db, "dev"), {})

        self.assertEqual(self.read_table_names(db), ["notes"])

    def test_list_excludes_unknown_keys(self):
        # 库内未知键不出现在结果中，也不影响已知键的输出。
        db = self.db_path("list_unknown_key")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)",
                    ("dev", "other_key", "true"),
                )
        finally:
            conn.close()

        self.assertListOk(self.list_flags(db, "dev"), {"new_ui": True})

    def test_list_after_unset_no_longer_shows_record(self):
        # 撤销后的记录不再出现在 list 中。
        db = self.db_path("list_after_unset")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.unset_flag(db, "dev", "new_ui"), "unset")

        self.assertListOk(self.list_flags(db, "dev"), {})

    def test_list_empty_env_rejected_before_storage(self):
        # 空串或全空白环境名报 EMPTY_ENV，即使数据库路径也不可用。
        missing_dir = os.path.join(self.tmpdir, "list_no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        self.assertCommandError(self.list_flags(db, ""), "EMPTY_ENV")
        self.assertCommandError(self.list_flags(db, "   "), "EMPTY_ENV")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_list_missing_parent_directory_reports_storage_error(self):
        # 父目录不存在：报 STORAGE_ERROR，不创建目录或文件。
        missing_dir = os.path.join(self.tmpdir, "list_missing_parent")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        self.assertCommandError(self.list_flags(db, "dev"), "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_list_plain_text_file_reports_storage_error(self):
        # 目标是普通文本文件而非 SQLite 库：报 STORAGE_ERROR，文件不变。
        db = self.db_path("list_plain_text")
        content = "this is not a sqlite database\njust fictional config\n"
        with open(db, "w", encoding="utf-8") as fh:
            fh.write(content)

        self.assertCommandError(self.list_flags(db, "dev"), "STORAGE_ERROR")

        with open(db, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), content)

    def test_list_flags_table_without_value_column_reports_storage_error(self):
        # flags 表缺少查询所需的 value 列：报 STORAGE_ERROR，表结构不变。
        db = self.db_path("list_no_value_column")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key) VALUES (?, ?)", ("qa", "new_ui")
                )
        finally:
            conn.close()

        self.assertCommandError(self.list_flags(db, "dev"), "STORAGE_ERROR")

        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("qa", "new_ui")])

    def test_list_invalid_stored_value_reports_storage_error(self):
        # 已知键存有 true/false 之外的值：报 STORAGE_ERROR，不输出部分结果。
        db = self.db_path("list_bad_value")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)",
                    ("dev", "new_ui", "yes"),
                )
        finally:
            conn.close()

        self.assertCommandError(self.list_flags(db, "dev"), "STORAGE_ERROR")

        self.assertEqual(
            self.read_flags_rows(db), [("dev", "new_ui", "yes")]
        )


class TestDiff(FlagctlCliTestCase):
    """diff 只读比较两个环境已知键的直接设置。

    输出协议：退出 0、stderr 为空、stdout 为单行 JSON 对象加换行；
    只输出两侧不同的已知键，值为 {"left": ..., "right": ...}，已设置
    是 JSON 布尔值、未设置是 null，false 与未设置明确区分。比较不
    创建数据库文件、目录或表，也不改动、修复或删除任何已有记录。
    """

    def assertDiffOk(self, proc, expected):
        """退出 0、stderr 为空、stdout 为单行 JSON 且解析结果等于 expected。"""
        self.assertEqual(
            proc.returncode, 0, "期望退出码 0，实际 stderr: %r" % proc.stderr
        )
        self.assertEqual(proc.stderr, "")
        self.assertTrue(proc.stdout.endswith("\n"), "输出必须以换行结束")
        body = proc.stdout[:-1]
        self.assertNotIn("\n", body, "JSON 输出必须只有一行")
        self.assertEqual(json.loads(body), expected)

    def seed_flags_table(self, db, rows):
        """直接建表并写入 (env, key, value) 行，绕过 set 的输入校验。"""
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.executemany(
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)",
                    rows,
                )
        finally:
            conn.close()

    def test_diff_reports_difference_in_input_order(self):
        # 主要固定样例：dev/new_ui=false、qa/new_ui=true。
        # diff dev qa 输出 {"new_ui":{"left":false,"right":true}}，
        # 交换环境顺序后左右值互换；两次查询后记录保持原样。
        db = self.db_path("diff_sample")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")

        self.assertDiffOk(
            self.diff_flags(db, "dev", "qa"),
            {"new_ui": {"left": False, "right": True}},
        )
        self.assertDiffOk(
            self.diff_flags(db, "qa", "dev"),
            {"new_ui": {"left": True, "right": False}},
        )

        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )

    def test_diff_same_bool_values_returns_empty_object(self):
        # 两侧布尔值相同（true 与 false 各验证一次）：输出 {}。
        db = self.db_path("diff_same")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        self.assertDiffOk(self.diff_flags(db, "dev", "qa"), {})

        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertDiffOk(self.diff_flags(db, "qa", "dev"), {})

    def test_diff_both_unset_returns_empty_object(self):
        # 两侧都未设置（库内只有其他环境的记录）：输出 {}，不新增记录。
        db = self.db_path("diff_both_unset")
        self.assertCommandOk(
            self.set_flag(db, "staging", "new_ui", "true"), "true"
        )

        self.assertDiffOk(self.diff_flags(db, "dev", "qa"), {})

        self.assertEqual(
            self.read_flags_rows(db), [("staging", "new_ui", "true")]
        )

    def test_diff_env_with_itself_returns_empty_object(self):
        # 同一环境自比：无论是否已设置都输出 {}。
        db = self.db_path("diff_self")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")

        self.assertDiffOk(self.diff_flags(db, "dev", "dev"), {})
        self.assertDiffOk(self.diff_flags(db, "qa", "qa"), {})

    def test_diff_unset_side_is_null_and_false_is_not_unset(self):
        # 一侧未设置时对应值为 null；已保存的 false 是有效值，
        # 与未设置比较时输出 {"left": false, "right": null} 而非 {}。
        db = self.db_path("diff_null_side")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")

        self.assertDiffOk(
            self.diff_flags(db, "dev", "qa"),
            {"new_ui": {"left": False, "right": None}},
        )
        self.assertDiffOk(
            self.diff_flags(db, "qa", "dev"),
            {"new_ui": {"left": None, "right": False}},
        )

        self.assertEqual(self.read_flags_rows(db), [("dev", "new_ui", "false")])

    def test_diff_env_surrounding_whitespace_stripped(self):
        # 环境名两端空白被去除：" dev " 与 dev 命中同一组记录。
        db = self.db_path("diff_whitespace")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")

        self.assertDiffOk(
            self.diff_flags(db, "  dev  ", "\tqa\t"),
            {"new_ui": {"left": True, "right": False}},
        )

    def test_diff_env_names_remain_case_sensitive(self):
        # 大小写仍敏感：Dev 与 dev 是两个不同的环境。
        db = self.db_path("diff_case")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")

        self.assertDiffOk(
            self.diff_flags(db, "Dev", "dev"),
            {"new_ui": {"left": None, "right": True}},
        )

    def test_diff_ignores_unknown_keys_and_other_envs(self):
        # 未知键及其他环境的异常值不参与比较，也不影响合法目标的结果；
        # 比较后全部原始记录（含异常值）逐行保持不变。
        db = self.db_path("diff_ignores_others")
        self.seed_flags_table(
            db,
            [
                ("dev", "new_ui", "false"),
                ("dev", "other_key", "yes"),
                ("qa", "new_ui", "true"),
                ("staging", "new_ui", "yes"),
            ],
        )
        before = self.read_flags_rows(db)

        self.assertDiffOk(
            self.diff_flags(db, "dev", "qa"),
            {"new_ui": {"left": False, "right": True}},
        )

        self.assertEqual(self.read_flags_rows(db), before)

    def test_diff_empty_env_rejected_before_storage(self):
        # 左右任一环境为空串或全空白：退出 2、stdout 为空、stderr 严格
        # 为 EMPTY_ENV 加换行；即使数据库父目录不存在也先报该错误，
        # 且不创建目录或文件。
        missing_dir = os.path.join(self.tmpdir, "diff_no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        for left, right in [("", "qa"), ("dev", ""), ("   ", "qa"), ("dev", " \t ")]:
            with self.subTest(left=left, right=right):
                proc = self.diff_flags(db, left, right)
                self.assertCommandError(proc, "EMPTY_ENV")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_diff_missing_db_returns_empty_object_without_creating_file(self):
        # 父目录存在但数据库文件缺失：查询成功并输出 {}，文件仍不存在。
        db = self.db_path("diff_missing")
        self.assertTrue(os.path.isdir(self.tmpdir))
        self.assertFalse(os.path.exists(db))

        self.assertDiffOk(self.diff_flags(db, "dev", "qa"), {})

        self.assertFalse(os.path.exists(db), "diff 不得创建数据库文件: %s" % db)

    def test_diff_valid_db_without_flags_table_returns_empty_object(self):
        # 有效库缺少 flags 表：查询成功并输出 {}，不补建表，
        # 原有表与数据保持不变。
        db = self.db_path("diff_no_flags_table")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO notes (text) VALUES ('sample note')")
        finally:
            conn.close()
        self.assertEqual(self.read_table_names(db), ["notes"])

        self.assertDiffOk(self.diff_flags(db, "dev", "qa"), {})

        self.assertEqual(self.read_table_names(db), ["notes"])
        conn = sqlite3.connect(db)
        try:
            notes = conn.execute("SELECT id, text FROM notes").fetchall()
        finally:
            conn.close()
        self.assertEqual(notes, [(1, "sample note")])

    def test_diff_missing_parent_directory_reports_storage_error(self):
        # 父目录不存在：报 STORAGE_ERROR，不创建目录或文件。
        missing_dir = os.path.join(self.tmpdir, "diff_missing_parent")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        self.assertCommandError(self.diff_flags(db, "dev", "qa"), "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_diff_plain_text_file_reports_storage_error(self):
        # 目标是普通文本文件而非 SQLite 库：报 STORAGE_ERROR，
        # 文件逐字节保持原样。
        db = self.db_path("diff_plain_text")
        content = b"this is not a sqlite database\njust fictional config\n"
        with open(db, "wb") as fh:
            fh.write(content)

        self.assertCommandError(self.diff_flags(db, "dev", "qa"), "STORAGE_ERROR")

        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), content)

    def test_diff_flags_table_without_value_column_reports_storage_error(self):
        # flags 表缺少查询所需的 value 列：报 STORAGE_ERROR，
        # 表结构与既有记录保持不变。
        db = self.db_path("diff_no_value_column")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key) VALUES (?, ?)", ("qa", "new_ui")
                )
        finally:
            conn.close()
        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("qa", "new_ui")])

        self.assertCommandError(self.diff_flags(db, "dev", "qa"), "STORAGE_ERROR")

        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("qa", "new_ui")])

    def test_diff_invalid_value_in_either_target_env_reports_storage_error(self):
        # 任一目标环境的 new_ui 存有 yes：报 STORAGE_ERROR，stdout 为空，
        # 不输出部分差异或堆栈；异常记录不被修复或删除。
        for label, rows in [
            ("diff_bad_left", [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")]),
            ("diff_bad_right", [("dev", "new_ui", "false"), ("qa", "new_ui", "yes")]),
        ]:
            with self.subTest(label=label):
                db = self.db_path(label)
                self.seed_flags_table(db, rows)
                before = self.read_flags_rows(db)

                proc = self.diff_flags(db, "dev", "qa")
                self.assertCommandError(proc, "STORAGE_ERROR")

                self.assertEqual(self.read_flags_rows(db), before)


class TestEnvs(FlagctlCliTestCase):
    """envs 列出库中已有合法键直接设置的环境名。

    输出协议：退出 0、stderr 为空、stdout 为单行 JSON 数组加换行；
    环境含有至少一个已知键的直接设置（值为 true 或 false，false 也算
    已设置）时才出现，同名去重并按名称 Unicode 码点字典序升序；名称
    按库中原文输出，不去除空白、不转换大小写。未知键的记录不参与
    判断，只含未知键的环境不出现，其异常值也不影响结果。查询全程
    只读，不创建目录、文件或 flags 表，也不改动任何记录。
    """

    def assertEnvsOk(self, proc, expected):
        """退出 0、stderr 为空、stdout 为单行 JSON 数组且解析等于 expected。"""
        self.assertEqual(
            proc.returncode, 0, "期望退出码 0，实际 stderr: %r" % proc.stderr
        )
        self.assertEqual(proc.stderr, "")
        self.assertTrue(proc.stdout.endswith("\n"), "输出必须以换行结束")
        body = proc.stdout[:-1]
        self.assertNotIn("\n", body, "JSON 输出必须只有一行")
        self.assertEqual(json.loads(body), expected)

    def seed_flags_table(self, db, rows):
        """直接建表并写入 (env, key, value) 行，绕过 set 的输入校验。"""
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.executemany(
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)",
                    rows,
                )
        finally:
            conn.close()

    def test_fixed_sample_lists_dev_and_qa_and_old_excluded(self):
        # 固定验收样例：dev/new_ui=false、qa/new_ui=true，另有只含未知
        # 键的 old 环境。envs 输出 ["dev", "qa"]；删除 dev 的 new_ui 后
        # 输出 ["qa"]，qa 设置不变；每次查询前后表结构与全部记录一致。
        db = self.db_path("envs_sample")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)",
                    ("old", "other_key", "true"),
                )
        finally:
            conn.close()
        before_rows = self.read_flags_rows(db)
        before_cols = self.read_flags_columns(db)
        self.assertEqual(
            before_rows,
            [
                ("dev", "new_ui", "false"),
                ("old", "other_key", "true"),
                ("qa", "new_ui", "true"),
            ],
        )

        self.assertEnvsOk(self.envs_flags(db), ["dev", "qa"])
        # 查询不改表结构、不动任何记录（含 old 的未知键行）。
        self.assertEqual(self.read_flags_columns(db), before_cols)
        self.assertEqual(self.read_flags_rows(db), before_rows)

        self.assertCommandOk(self.unset_flag(db, "dev", "new_ui"), "unset")
        self.assertEnvsOk(self.envs_flags(db), ["qa"])
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")
        self.assertEqual(
            self.read_flags_rows(db),
            [("old", "other_key", "true"), ("qa", "new_ui", "true")],
        )

    def test_false_counts_as_set(self):
        # 直接设置为 false 的环境照样出现。
        db = self.db_path("envs_false")
        self.seed_flags_table(db, [("dev", "new_ui", "false")])
        self.assertEnvsOk(self.envs_flags(db), ["dev"])

    def test_env_with_only_unknown_key_is_absent(self):
        # 只有未知键（哪怕值为 true）的环境不出现；合法环境不受影响。
        db = self.db_path("envs_only_unknown")
        self.seed_flags_table(
            db,
            [
                ("old", "other_key", "true"),
                ("qa", "new_ui", "false"),
                ("ancient", "mystery", "yes"),
            ],
        )
        self.assertEnvsOk(self.envs_flags(db), ["qa"])
        self.assertEqual(
            self.read_flags_rows(db),
            [
                ("ancient", "mystery", "yes"),
                ("old", "other_key", "true"),
                ("qa", "new_ui", "false"),
            ],
        )

    def test_unknown_key_anomalous_value_does_not_error(self):
        # 未知键上的异常值（yes、空串等）既不令环境出现，也不触发
        # STORAGE_ERROR；含合法键的环境正常列出。
        db = self.db_path("envs_unknown_bad")
        self.seed_flags_table(
            db,
            [
                ("old", "other_key", "yes"),
                ("dev", "new_ui", "true"),
                ("dev", "mystery", ""),
            ],
        )
        self.assertEnvsOk(self.envs_flags(db), ["dev"])

    def test_dedup_and_codepoint_ordering_and_verbatim_names(self):
        # 同名环境去重；按名称 Unicode 码点字典序升序；区分大小写、
        # 保留两端空白、中文原样输出。
        db = self.db_path("envs_order")
        self.seed_flags_table(
            db,
            [
                ("qa", "new_ui", "true"),
                ("dev", "new_ui", "false"),
                ("dev", "other_key", "x"),
                ("Dev", "new_ui", "false"),
                (" dev", "new_ui", "true"),
                ("中文环境", "new_ui", "true"),
            ],
        )
        expected = sorted({"qa", "dev", "Dev", " dev", "中文环境"})
        self.assertEnvsOk(self.envs_flags(db), expected)

    def test_missing_db_returns_empty_array_without_creating_file(self):
        # 父目录存在但数据库文件缺失：输出 []，文件仍不存在。
        db = self.db_path("envs_missing")
        self.assertTrue(os.path.isdir(self.tmpdir))
        self.assertFalse(os.path.exists(db))

        self.assertEnvsOk(self.envs_flags(db), [])

        self.assertFalse(os.path.exists(db), "envs 不得创建数据库文件: %s" % db)

    def test_valid_db_without_flags_table_returns_empty_array(self):
        # 有效库缺少 flags 表：输出 []，不补建表，原有表与数据不变。
        db = self.db_path("envs_no_flags_table")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO notes (text) VALUES ('sample note')")
        finally:
            conn.close()

        self.assertEnvsOk(self.envs_flags(db), [])

        self.assertEqual(self.read_table_names(db), ["notes"])
        conn = sqlite3.connect(db)
        try:
            notes = conn.execute("SELECT id, text FROM notes").fetchall()
        finally:
            conn.close()
        self.assertEqual(notes, [(1, "sample note")])

    def test_empty_flags_table_returns_empty_array(self):
        # flags 表存在但没有任何记录：输出 []。
        db = self.db_path("envs_empty_table")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
        finally:
            conn.close()

        self.assertEnvsOk(self.envs_flags(db), [])
        self.assertEqual(self.read_flags_columns(db), ["env", "key", "value"])

    def test_missing_parent_directory_reports_storage_error(self):
        # 父目录不存在：报 STORAGE_ERROR，不创建目录或文件。
        missing_dir = os.path.join(self.tmpdir, "envs_no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        self.assertCommandError(self.envs_flags(db), "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_plain_text_file_reports_storage_error(self):
        # 目标是普通文本文件而非 SQLite 库：报 STORAGE_ERROR，文件不变。
        db = self.db_path("envs_plain_text")
        content = b"this is not a sqlite database\njust fictional config\n"
        with open(db, "wb") as fh:
            fh.write(content)

        self.assertCommandError(self.envs_flags(db), "STORAGE_ERROR")

        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), content)

    def test_missing_required_column_reports_storage_error(self):
        # flags 表缺少 env、key、value 中任一列都报 STORAGE_ERROR，
        # 表结构保持不变。
        for label, schema in [
            ("envs_no_env", "CREATE TABLE flags (key TEXT, value TEXT)"),
            ("envs_no_key", "CREATE TABLE flags (env TEXT, value TEXT)"),
            (
                "envs_no_value",
                "CREATE TABLE flags (env TEXT NOT NULL, key TEXT NOT NULL, "
                "PRIMARY KEY (env, key))",
            ),
        ]:
            with self.subTest(label=label):
                db = self.db_path(label)
                conn = sqlite3.connect(db)
                try:
                    with conn:
                        conn.execute(schema)
                finally:
                    conn.close()
                cols_before = self.read_flags_columns(db)

                self.assertCommandError(self.envs_flags(db), "STORAGE_ERROR")

                self.assertEqual(self.read_flags_columns(db), cols_before)

    def test_invalid_known_key_value_reports_storage_error_without_partial_list(self):
        # 库中任一已知键存有 true/false 之外的值：报 STORAGE_ERROR，
        # stdout 为空、不输出部分名单；异常数据原样保留，不被修复。
        db = self.db_path("envs_bad_value")
        self.seed_flags_table(
            db,
            [
                ("dev", "new_ui", "true"),
                ("qa", "new_ui", "yes"),
                ("old", "other_key", "false"),
            ],
        )
        before = self.read_flags_rows(db)

        self.assertCommandError(self.envs_flags(db), "STORAGE_ERROR")

        self.assertEqual(self.read_flags_rows(db), before)

    def test_extra_arguments_are_rejected(self):
        # envs 不接收环境名或键名：多余位置参数被拒绝（退出码非 0），
        # 且拒绝参数不影响既有数据。
        db = self.db_path("envs_extra_arg")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        proc = self.run_flagctl(db, "envs", "dev")
        self.assertNotEqual(proc.returncode, 0)
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")


class TestImport(FlagctlCliTestCase):
    """import 从 JSON 文件导入一个环境的直接设置。

    输出协议：成功时退出 0、stderr 为空、stdout 为仅含本次导入项的
    单行 JSON 对象加换行；失败时退出 2、stdout 为空、stderr 仅为
    错误码加换行。校验顺序为环境名 -> 读文件 -> JSON 解析 -> 全部
    键名 -> 全部布尔值，全部通过后才访问数据库。
    """

    def assertImportOk(self, proc, expected):
        """退出 0、stderr 为空、stdout 为单行 JSON 且解析结果等于 expected。"""
        self.assertEqual(
            proc.returncode, 0, "期望退出码 0，实际 stderr: %r" % proc.stderr
        )
        self.assertEqual(proc.stderr, "")
        self.assertTrue(proc.stdout.endswith("\n"), "输出必须以换行结束")
        body = proc.stdout[:-1]
        self.assertNotIn("\n", body, "JSON 输出必须只有一行")
        self.assertEqual(json.loads(body), expected)

    def test_fixed_sample_overwrites_dev_and_keeps_qa(self):
        # 固定验收样例：dev/new_ui=true、qa/new_ui=true，导入
        # {"new_ui": false} 后 dev 变为 false，qa 保持 true。
        db = self.db_path("import_sample")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        path = self.write_import_file('{"new_ui":false}')

        self.assertImportOk(self.import_flags(db, "dev", path), {"new_ui": False})

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )

    def test_string_false_rejected_and_values_unchanged(self):
        # 固定验收样例：{"new_ui": "false"} 报 INVALID_BOOL，既有值不变。
        db = self.db_path("import_string_bool")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        path = self.write_import_file('{"new_ui":"false"}')

        self.assertCommandError(self.import_flags(db, "dev", path), "INVALID_BOOL")

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_import_creates_db_and_table_on_fresh_path(self):
        # 非空导入沿用 set 的行为：在全新路径上创建数据库文件和 flags 表。
        db = self.db_path("import_fresh")
        self.assertFalse(os.path.exists(db))
        path = self.write_import_file('{"new_ui": true}')

        self.assertImportOk(self.import_flags(db, "dev", path), {"new_ui": True})

        self.assertTrue(os.path.isfile(db))
        self.assertEqual(self.read_table_names(db), ["flags"])
        self.assertEqual(self.read_flags_rows(db), [("dev", "new_ui", "true")])

    def test_empty_object_succeeds_without_touching_db(self):
        # 空对象 {} 成功返回 {}，不访问数据库：全新路径上不创建文件。
        db = self.db_path("import_empty")
        self.assertFalse(os.path.exists(db))
        path = self.write_import_file("{}")

        self.assertImportOk(self.import_flags(db, "dev", path), {})

        self.assertFalse(os.path.exists(db), "空对象导入不得创建数据库文件: %s" % db)

    def test_empty_env_rejected_before_reading_file(self):
        # 空或全空白环境名先报 EMPTY_ENV：即使文件不存在也不报
        # IMPORT_READ_ERROR，且不创建数据库文件。
        db = self.db_path("import_empty_env")
        missing_file = os.path.join(self.tmpdir, "no_such_settings.json")
        self.assertFalse(os.path.exists(missing_file))

        for env in ["", "   "]:
            with self.subTest(env=env):
                self.assertCommandError(
                    self.import_flags(db, env, missing_file), "EMPTY_ENV"
                )

        self.assertFalse(os.path.exists(db), "校验失败不得创建数据库文件: %s" % db)

    def test_env_surrounding_whitespace_matches_stripped(self):
        # 带两端空白的环境名导入后命中去除空白后的同一环境。
        db = self.db_path("import_whitespace")
        path = self.write_import_file('{"new_ui": false}')

        self.assertImportOk(self.import_flags(db, "  dev  ", path), {"new_ui": False})

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")

    def test_missing_file_reports_import_read_error(self):
        # 文件不存在：报 IMPORT_READ_ERROR，不创建数据库文件。
        db = self.db_path("import_missing_file")
        missing_file = os.path.join(self.tmpdir, "no_such_settings.json")
        self.assertFalse(os.path.exists(missing_file))

        self.assertCommandError(
            self.import_flags(db, "dev", missing_file), "IMPORT_READ_ERROR"
        )

        self.assertFalse(os.path.exists(db), "读文件失败不得创建数据库: %s" % db)

    def test_unreadable_file_reports_import_read_error(self):
        # 目标路径是目录（无法作为文件读取）：报 IMPORT_READ_ERROR。
        db = self.db_path("import_unreadable")
        directory = os.path.join(self.tmpdir, "settings_dir")
        os.mkdir(directory)

        self.assertCommandError(
            self.import_flags(db, "dev", directory), "IMPORT_READ_ERROR"
        )

        self.assertFalse(os.path.exists(db), "读文件失败不得创建数据库: %s" % db)

    def test_invalid_utf8_reports_invalid_json(self):
        # 非法 UTF-8 字节序列：报 INVALID_JSON。
        db = self.db_path("import_bad_utf8")
        path = self.write_import_file(b'{"new_ui": \xff\xfe}', raw=True)

        self.assertCommandError(self.import_flags(db, "dev", path), "INVALID_JSON")

        self.assertFalse(os.path.exists(db), "校验失败不得创建数据库: %s" % db)

    def test_syntax_error_reports_invalid_json(self):
        # JSON 语法错误：报 INVALID_JSON。
        db = self.db_path("import_syntax")
        path = self.write_import_file('{"new_ui": true')

        self.assertCommandError(self.import_flags(db, "dev", path), "INVALID_JSON")

        self.assertFalse(os.path.exists(db), "校验失败不得创建数据库: %s" % db)

    def test_top_level_non_object_reports_invalid_json(self):
        # 顶层不是对象（数组、布尔、字符串）：均报 INVALID_JSON。
        for index, content in enumerate(["[]", "true", '"new_ui"', "42"]):
            with self.subTest(content=content):
                db = self.db_path("import_non_object_%d" % index)
                path = self.write_import_file(content, name="non_obj_%d.json" % index)

                self.assertCommandError(
                    self.import_flags(db, "dev", path), "INVALID_JSON"
                )

                self.assertFalse(
                    os.path.exists(db), "校验失败不得创建数据库: %s" % db
                )

    def test_duplicate_keys_report_invalid_json(self):
        # 存在重复键：报 INVALID_JSON（标准 json.loads 默认会静默覆盖，
        # 这里必须拒绝）。
        db = self.db_path("import_dup_keys")
        path = self.write_import_file('{"new_ui": true, "new_ui": false}')

        self.assertCommandError(self.import_flags(db, "dev", path), "INVALID_JSON")

        self.assertFalse(os.path.exists(db), "校验失败不得创建数据库: %s" % db)

    def test_unknown_key_rejected(self):
        # 未知键报 UNKNOWN_KEY，既有记录保持不变。
        db = self.db_path("import_unknown_key")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        path = self.write_import_file('{"other_key": true}')

        self.assertCommandError(self.import_flags(db, "dev", path), "UNKNOWN_KEY")

        self.assertEqual(self.read_flags_rows(db), [("dev", "new_ui", "true")])

    def test_unknown_key_takes_precedence_over_invalid_bool(self):
        # 键名与值同时不合法：先校验全部键名，报 UNKNOWN_KEY。
        db = self.db_path("import_key_before_bool")
        path = self.write_import_file('{"other_key": "true"}')

        self.assertCommandError(self.import_flags(db, "dev", path), "UNKNOWN_KEY")

        self.assertFalse(os.path.exists(db), "校验失败不得创建数据库: %s" % db)

    def test_non_bool_values_rejected(self):
        # 数字、null、字符串、嵌套对象均不是 JSON 布尔值：报 INVALID_BOOL。
        for index, content in enumerate(
            ['{"new_ui": 1}', '{"new_ui": 0}', '{"new_ui": null}',
             '{"new_ui": "true"}', '{"new_ui": {}}']
        ):
            with self.subTest(content=content):
                db = self.db_path("import_bad_bool_%d" % index)
                path = self.write_import_file(
                    content, name="bad_bool_%d.json" % index
                )

                self.assertCommandError(
                    self.import_flags(db, "dev", path), "INVALID_BOOL"
                )

                self.assertFalse(
                    os.path.exists(db), "校验失败不得创建数据库: %s" % db
                )

    def test_bare_constants_reported_as_invalid_json(self):
        # 未加引号的 NaN/Infinity/-Infinity 不是合法 JSON：无论出现在
        # 顶层、对象值还是嵌套结构中，都在键名及布尔值校验之前报
        # INVALID_JSON（即使外层是未知键，也不得报 UNKNOWN_KEY）。
        contents = [
            '{"new_ui": NaN}',
            '{"new_ui": Infinity}',
            '{"new_ui": -Infinity}',
            '{"other_key": [NaN]}',
            '{"other_key": [Infinity]}',
            '{"new_ui": {"a": -Infinity}}',
            '{"new_ui": [[[{"x": [NaN]}]]]}',
            'NaN',
            'Infinity',
            '-Infinity',
            '[NaN]',
            '{"new_ui": true, "new_ui": NaN}',
        ]
        for index, content in enumerate(contents):
            with self.subTest(content=content):
                db = self.db_path("import_constant_%d" % index)
                path = self.write_import_file(
                    content, name="constant_%d.json" % index
                )

                self.assertCommandError(
                    self.import_flags(db, "dev", path), "INVALID_JSON"
                )

                self.assertFalse(
                    os.path.exists(db), "校验失败不得创建数据库: %s" % db
                )

    def test_bare_constant_rejected_and_values_unchanged(self):
        # 固定验收样例：{"new_ui": NaN} 报 INVALID_JSON，既有值保持
        # true，其他环境的记录也不受影响。
        db = self.db_path("import_constant_keep")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        path = self.write_import_file('{"new_ui": NaN}')

        self.assertCommandError(self.import_flags(db, "dev", path), "INVALID_JSON")

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_quoted_constants_and_large_exponents_not_syntax_errors(self):
        # 引号内的 NaN/Infinity 只是普通字符串，按值校验报 INVALID_BOOL；
        # 键名是 NaN 仍报 UNKNOWN_KEY；合法数字（含 1e999 溢出为
        # Infinity）属于合法 JSON 词法，作为开关值报 INVALID_BOOL。
        cases = [
            ('{"new_ui": "NaN"}', "INVALID_BOOL"),
            ('{"new_ui": "Infinity"}', "INVALID_BOOL"),
            ('{"new_ui": "-Infinity"}', "INVALID_BOOL"),
            ('{"NaN": true}', "UNKNOWN_KEY"),
            ('{"new_ui": 1e999}', "INVALID_BOOL"),
        ]
        for index, (content, code) in enumerate(cases):
            with self.subTest(content=content):
                db = self.db_path("import_constant_quote_%d" % index)
                path = self.write_import_file(
                    content, name="constant_quote_%d.json" % index
                )

                self.assertCommandError(self.import_flags(db, "dev", path), code)

                self.assertFalse(
                    os.path.exists(db), "校验失败不得创建数据库: %s" % db
                )

    def test_missing_parent_directory_reports_storage_error(self):
        # 非空导入遇到父目录不存在：报 STORAGE_ERROR，不创建目录或文件。
        missing_dir = os.path.join(self.tmpdir, "import_no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        path = self.write_import_file('{"new_ui": true}')

        self.assertCommandError(self.import_flags(db, "dev", path), "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_plain_text_db_reports_storage_error(self):
        # 目标是普通文本文件而非 SQLite 库：报 STORAGE_ERROR，文件不变。
        db = self.db_path("import_plain_text")
        content = b"this is not a sqlite database\njust fictional config\n"
        with open(db, "wb") as fh:
            fh.write(content)
        path = self.write_import_file('{"new_ui": true}')

        self.assertCommandError(self.import_flags(db, "dev", path), "STORAGE_ERROR")

        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), content)

    def test_flags_table_without_value_column_reports_storage_error(self):
        # flags 表缺少所需列：报 STORAGE_ERROR，表结构与既有记录不变。
        db = self.db_path("import_no_value_column")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key) VALUES (?, ?)", ("qa", "new_ui")
                )
        finally:
            conn.close()
        path = self.write_import_file('{"new_ui": true}')

        self.assertCommandError(self.import_flags(db, "dev", path), "STORAGE_ERROR")

        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("qa", "new_ui")])

    def test_import_preserves_unmentioned_records(self):
        # 导入只覆盖目标环境中出现的同名键：其他环境和未出现的记录
        # 保持原样（库中未知键记录也不受影响）。
        db = self.db_path("import_preserves")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)",
                    ("dev", "other_key", "true"),
                )
        finally:
            conn.close()
        path = self.write_import_file('{"new_ui": false}')

        self.assertImportOk(self.import_flags(db, "dev", path), {"new_ui": False})

        self.assertEqual(
            self.read_flags_rows(db),
            [
                ("dev", "new_ui", "false"),
                ("dev", "other_key", "true"),
                ("qa", "new_ui", "false"),
            ],
        )


if __name__ == "__main__":
    unittest.main()