#!/usr/bin/env python3
"""flagctl.py 的命令行回归测试。

覆盖本地布尔开关 set/get 在进程间的持久化行为，以及 set 的输入校验：

* 合法设置跨进程持久化、覆盖写、环境之间互不影响；
* 环境名两端空白按去除空白后的名称落库；
* 读取尚未创建的数据库得到 VALUE_NOT_SET 且不创建文件；
* get 在四种数据库状态下的行为：有效库缺 flags 表、flags 表无对应
  环境记录（VALUE_NOT_SET），父目录不存在、目标为普通文本文件
  （STORAGE_ERROR），且读取前后相关数据与文件状态保持不变；
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
* set 写入失败：父目录不存在、目标为普通文本文件、flags 表缺少
  value 列三种状态均报 STORAGE_ERROR，且目录、文件内容、表结构与
  既有记录保持原样；
* 父目录不存在时 set 的输入校验先于存储访问：EMPTY_ENV /
  UNKNOWN_KEY / INVALID_BOOL 各自唯一报错，不创建目录或文件。

所有业务调用均以子进程执行 ``python flagctl.py --db <临时数据库>``，
每个用例使用独立的临时目录，结束后自动清理，只依赖 Python 3 标准库。
可在项目目录直接运行::

    python -m unittest discover
    python -m unittest test_flagctl.TestPersistence.test_set_true_persists_across_processes
"""

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


class TestSetStorageFailures(FlagctlCliTestCase):
    """set 写入失败：三种存储状态均报 STORAGE_ERROR 且现场保持不变。

    统一执行合法的 set dev new_ui true：

    * 父目录不存在——报错后不创建目录或数据库文件；
    * 目标是普通文本文件——报错后文件内容逐字节保持原样；
    * 有效 SQLite 库但 flags 表只有 env、key 两列（预存 qa/new_ui）——
      报错后表结构与既有记录保持不变，不新增 dev 记录。
    """

    def test_missing_parent_directory_reports_storage_error(self):
        # 父目录不存在：报 STORAGE_ERROR，且不创建目录或文件。
        missing_dir = os.path.join(self.tmpdir, "no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))

        proc = self.set_flag(db, "dev", "new_ui", "true")
        self.assertCommandError(proc, "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_plain_text_file_reports_storage_error(self):
        # 目标是已存在的普通文本文件而非 SQLite 库：报 STORAGE_ERROR，
        # 原文件内容逐字节保持原样。
        db = self.db_path("plain_text")
        content = b"this is not a sqlite database\njust fictional config\n"
        with open(db, "wb") as fh:
            fh.write(content)

        proc = self.set_flag(db, "dev", "new_ui", "true")
        self.assertCommandError(proc, "STORAGE_ERROR")

        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), content)

    def test_flags_table_missing_value_column_reports_storage_error(self):
        # 有效 SQLite 库的 flags 表只有 env、key 两列并预存 qa/new_ui：
        # 报 STORAGE_ERROR，表结构与既有记录保持不变，不新增 dev 记录。
        db = self.db_path("missing_value_column")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key) VALUES ('qa', 'new_ui')"
                )
        finally:
            conn.close()

        proc = self.set_flag(db, "dev", "new_ui", "true")
        self.assertCommandError(proc, "STORAGE_ERROR")

        conn = sqlite3.connect(db)
        try:
            columns = [
                row[1] for row in conn.execute("PRAGMA table_info(flags)")
            ]
            rows = conn.execute(
                "SELECT env, key FROM flags ORDER BY env, key"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(columns, ["env", "key"], "表结构不得改变")
        self.assertEqual(rows, [("qa", "new_ui")], "既有记录保持原样且不新增 dev")


class TestSetValidationBeforeStorage(FlagctlCliTestCase):
    """父目录不存在时，set 的输入校验先于存储访问。

    每种非法输入都在父目录不存在的路径上验证：唯一报错为对应的校验
    错误码（不降级为 STORAGE_ERROR），且不创建缺失的目录或数据库文件。
    """

    def assert_set_rejected_on_missing_parent(self, label, env, key, value, code):
        """父目录不存在：校验错误优先，且不创建目录或文件。"""
        missing_dir = os.path.join(self.tmpdir, label + "_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        proc = self.set_flag(db, env, key, value)
        self.assertCommandError(proc, code)
        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_empty_env_takes_precedence_over_key_and_bool(self):
        # 环境名仅为空格，键和值同时不合法：唯一错误为 EMPTY_ENV。
        self.assert_set_rejected_on_missing_parent(
            "empty_env_first", "   ", "bad_key", "TRUE", "EMPTY_ENV"
        )

    def test_unknown_key_takes_precedence_over_bool(self):
        # 环境合法而键、值均不合法：唯一错误为 UNKNOWN_KEY。
        self.assert_set_rejected_on_missing_parent(
            "unknown_key_first", "dev", "other_key", "TRUE", "UNKNOWN_KEY"
        )

    def test_invalid_bool_rejected(self):
        # 环境与键均合法、值不是小写布尔：唯一错误为 INVALID_BOOL。
        self.assert_set_rejected_on_missing_parent(
            "invalid_bool", "dev", "new_ui", "TRUE", "INVALID_BOOL"
        )


if __name__ == "__main__":
    unittest.main()
