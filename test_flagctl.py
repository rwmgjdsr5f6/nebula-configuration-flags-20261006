#!/usr/bin/env python3
"""flagctl.py 的命令行回归测试。

覆盖本地布尔开关 set/get 在进程间的持久化行为，以及 set 的输入校验：

* 合法设置跨进程持久化、覆盖写、环境之间互不影响；
* 环境名两端空白按去除空白后的名称落库；
* 读取尚未创建的数据库得到 VALUE_NOT_SET 且不创建文件；
* get 在四种数据库状态下的行为：有效库缺 flags 表、flags 表无对应
  环境记录（VALUE_NOT_SET），父目录不存在、目标为普通文本文件
  （STORAGE_ERROR），且读取前后相关数据与文件状态保持不变；
* get 对既有记录内容的校验：固定样例 dev/new_ui=yes 报 STORAGE_ERROR、
  qa/new_ui=false 输出 false，其余非法文本（TRUE、1、空字符串、带空白
  的 true 等）同样拒绝，合法 true/false 正常读取，其他环境的异常值
  不影响合法目标值，查询前后全部记录保持不变；
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
  STORAGE_ERROR，且不创建目录或文件。

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


class TestGetStoredValues(FlagctlCliTestCase):
    """get 对既有记录内容的校验：成功结果只能是严格的 true 或 false。

    非法存储值（yes、TRUE、1、空字符串、带空白的 true 等）通过 SQL
    直接预置（set 本身拒绝这些输入）；读取时一律报 STORAGE_ERROR，
    退出 2、stdout 为空、stderr 严格为 ``STORAGE_ERROR\\n``，不输出
    原值或异常堆栈，且查询前后全部记录保持不变、不新增表或记录。
    校验只针对目标记录：其他环境的异常值不影响合法目标值的读取。
    """

    def create_flags_db(self, label, rows):
        """创建只含 flags 表的数据库并直接写入给定的 (env, key, value) 行。"""
        db = self.db_path(label)
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.executemany(
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)", rows
                )
        finally:
            conn.close()
        return db

    def test_fixed_sample_yes_reports_storage_error_false_reads_ok(self):
        # 固定样例：dev/new_ui=yes 与 qa/new_ui=false。读 dev 报
        # STORAGE_ERROR，读 qa 输出 false；两次查询前后两条记录保持相同。
        db = self.create_flags_db(
            "get_fixed_sample",
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")],
        )
        before = self.read_flags_rows(db)
        self.assertEqual(
            before, [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")]
        )

        bad = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(bad, "STORAGE_ERROR")
        good = self.get_flag(db, "qa", "new_ui")
        self.assertCommandOk(good, "false")

        self.assertEqual(self.read_flags_rows(db), before)
        # 再次读取结果一致：失败查询没有改变任何记录。
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "STORAGE_ERROR")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")
        self.assertEqual(self.read_flags_rows(db), before)

    def test_invalid_stored_values_report_storage_error(self):
        # 每种非法文本单独一个库：报错且不改动记录。
        invalid_values = ["yes", "TRUE", "True", "1", "0", "", " true ", "true "]
        for index, stored in enumerate(invalid_values):
            db = self.create_flags_db(
                "get_invalid_%d" % index, [("dev", "new_ui", stored)]
            )
            with self.subTest(stored=stored):
                proc = self.get_flag(db, "dev", "new_ui")
                self.assertCommandError(proc, "STORAGE_ERROR")
                self.assertEqual(
                    self.read_flags_rows(db), [("dev", "new_ui", stored)]
                )

    def test_valid_stored_values_read_ok(self):
        # 严格的 true 与 false 均可成功读取，原值加换行输出。
        for stored in ("true", "false"):
            db = self.create_flags_db(
                "get_valid_" + stored, [("dev", "new_ui", stored)]
            )
            with self.subTest(stored=stored):
                self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), stored)
                self.assertEqual(
                    self.read_flags_rows(db), [("dev", "new_ui", stored)]
                )

    def test_invalid_value_in_other_env_does_not_affect_target(self):
        # qa 存有非法值不影响读取 dev 的合法 true；读取 dev 也不改动 qa。
        db = self.create_flags_db(
            "get_other_env_bad",
            [("dev", "new_ui", "true"), ("qa", "new_ui", "yes")],
        )

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")

        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "yes")],
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
                    "INSERT INTO flags (env, key) VALUES (?, ?)", ("qa", "new_ui")
                )
        finally:
            conn.close()
        self.assertEqual(self.read_flags_columns(db), ["env", "key"])

        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")

        self.assertEqual(self.read_flags_columns(db), ["env", "key"])
        self.assertEqual(self.read_flags_env_key_rows(db), [("qa", "new_ui")])


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


if __name__ == "__main__":
    unittest.main()