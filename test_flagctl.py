#!/usr/bin/env python3
"""flagctl.py 的命令行回归测试。

覆盖本地布尔开关 set/get 在进程间的持久化行为，以及 set 的输入校验：

* 合法设置跨进程持久化、覆盖写、环境之间互不影响；
* 环境名两端空白按去除空白后的名称落库；
* 读取尚未创建的数据库得到 VALUE_NOT_SET 且不创建文件；
* EMPTY_ENV / UNKNOWN_KEY / INVALID_BOOL 的拒绝顺序与输出协议。

所有业务调用均以子进程执行 ``python flagctl.py --db <临时数据库>``，
每个用例使用独立的临时目录，结束后自动清理，只依赖 Python 3 标准库。
可在项目目录直接运行::

    python -m unittest discover
    python -m unittest test_flagctl.TestPersistence.test_set_true_persists_across_processes
"""

import os
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


if __name__ == "__main__":
    unittest.main()
