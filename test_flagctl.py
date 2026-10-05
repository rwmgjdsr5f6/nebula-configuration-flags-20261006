#!/usr/bin/env python3
"""flagctl.py 的命令行回归测试。

在项目目录执行全部用例::

    python -m unittest discover

单独执行某个用例::

    python -m unittest test_flagctl.FlagctlCliTestCase.test_set_true_persists

仅依赖 Python 3 标准库。每个用例都在独立的可写临时目录中使用全新的
SQLite 数据库文件，结束后自动清理，不会触碰使用者已有的配置或数据库；
所有业务调用均以 ``python flagctl.py --db <临时数据库>`` 子进程方式发起，
因此读取校验验证的是跨进程持久化，而非同进程内的函数返回值。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
FLAGCTL = os.path.join(ROOT_DIR, "flagctl.py")

EXIT_OK = 0
EXIT_ERROR = 2


class FlagctlCliTestCase(unittest.TestCase):
    def setUp(self):
        # 每个用例独享临时目录与数据库，重复执行互不影响。
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "flags.db")

    def run_flagctl(self, *args, db_path=None):
        """以新进程调用 flagctl.py，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            FLAGCTL,
            "--db",
            db_path if db_path is not None else self.db_path,
            *args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True)

    def assert_success(self, proc, expected_stdout):
        """成功调用：退出 0，标准输出严格匹配，标准错误为空。"""
        self.assertEqual(
            proc.returncode,
            EXIT_OK,
            msg=f"stderr 意外输出: {proc.stderr!r}",
        )
        self.assertEqual(proc.stdout, expected_stdout)
        self.assertEqual(proc.stderr, "")

    def assert_error(self, proc, code):
        """失败调用：退出 2，标准输出为空，标准错误仅含错误码加一个换行。"""
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, code + "\n")

    def assert_rejected_on_fresh_and_seeded_db(self, set_args, code):
        """同一组非法 set 参数在两种数据库状态下都必须被拒绝。

        1. 全新路径：报错且不得留下数据库文件；
        2. 已保存 dev/new_ui=false 的库：报错后重新读取仍为 false。
        """
        # 全新路径：文件原本不存在，失败后也不得被创建。
        self.assertFalse(os.path.exists(self.db_path))
        proc = self.run_flagctl("set", *set_args)
        self.assert_error(proc, code)
        self.assertFalse(os.path.exists(self.db_path))

        # 已有数据的库：先写入 false，再发起非法调用。
        seed = self.run_flagctl("set", "dev", "new_ui", "false")
        self.assert_success(seed, "false\n")
        proc = self.run_flagctl("set", *set_args)
        self.assert_error(proc, code)

        # 保存结果保持不变。
        check = self.run_flagctl("get", "dev", "new_ui")
        self.assert_success(check, "false\n")

    # -- 成功样例 -----------------------------------------------------

    def test_set_true_persists_across_processes(self):
        # 首次设置 dev/new_ui=true：退出 0，标准输出严格为 true 加换行。
        proc = self.run_flagctl("set", "dev", "new_ui", "true")
        self.assert_success(proc, "true\n")
        self.assertTrue(os.path.exists(self.db_path))

        # 由全新进程读取同一环境与键，得到相同结果。
        proc = self.run_flagctl("get", "dev", "new_ui")
        self.assert_success(proc, "true\n")

    def test_overwrite_from_true_to_false_persists(self):
        proc = self.run_flagctl("set", "dev", "new_ui", "true")
        self.assert_success(proc, "true\n")

        proc = self.run_flagctl("set", "dev", "new_ui", "false")
        self.assert_success(proc, "false\n")

        # 改值由新进程读取确认。
        proc = self.run_flagctl("get", "dev", "new_ui")
        self.assert_success(proc, "false\n")

    def test_distinct_envs_do_not_overwrite_each_other(self):
        self.assert_success(
            self.run_flagctl("set", "dev", "new_ui", "false"), "false\n"
        )
        self.assert_success(
            self.run_flagctl("set", "qa", "new_ui", "true"), "true\n"
        )

        self.assert_success(
            self.run_flagctl("get", "dev", "new_ui"), "false\n"
        )
        self.assert_success(
            self.run_flagctl("get", "qa", "new_ui"), "true\n"
        )

    def test_env_with_surrounding_whitespace_maps_to_stripped_record(self):
        # 以带两端空白的环境名写入，应与去除空白后的 dev 对应同一条记录。
        self.assert_success(
            self.run_flagctl("set", "  dev  ", "new_ui", "true"), "true\n"
        )

        # 新进程分别用去除空白和带空白的名称读取，结果一致。
        self.assert_success(
            self.run_flagctl("get", "dev", "new_ui"), "true\n"
        )
        self.assert_success(
            self.run_flagctl("get", "\tdev\t", "new_ui"), "true\n"
        )

        # 数据库中只存在归一化后的唯一一条 dev 记录。
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT env, key, value FROM flags"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [("dev", "new_ui", "true")])

        # 反向再验一次：用不带空白的名称改 false，带空白读取仍命中同一记录。
        self.assert_success(
            self.run_flagctl("set", "dev", "new_ui", "false"), "false\n"
        )
        self.assert_success(
            self.run_flagctl("get", "  dev  ", "new_ui"), "false\n"
        )
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT env, key, value FROM flags"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [("dev", "new_ui", "false")])

    def test_get_from_missing_database_file_is_value_not_set(self):
        # 父目录存在但数据库文件尚未创建。
        self.assertTrue(os.path.isdir(self._tmp.name))
        self.assertFalse(os.path.exists(self.db_path))

        proc = self.run_flagctl("get", "dev", "new_ui")
        self.assert_error(proc, "VALUE_NOT_SET")

        # 读取缺库不得创建数据库文件。
        self.assertFalse(os.path.exists(self.db_path))

    # -- 错误样例：EMPTY_ENV ------------------------------------------

    def test_empty_env_empty_string_rejected(self):
        self.assert_rejected_on_fresh_and_seeded_db(
            ("", "new_ui", "true"), "EMPTY_ENV"
        )

    def test_empty_env_whitespace_only_rejected(self):
        self.assert_rejected_on_fresh_and_seeded_db(
            ("  \t ", "new_ui", "true"), "EMPTY_ENV"
        )

    # -- 错误样例：UNKNOWN_KEY ----------------------------------------

    def test_unknown_key_rejected(self):
        self.assert_rejected_on_fresh_and_seeded_db(
            ("dev", "other_key", "true"), "UNKNOWN_KEY"
        )

    # -- 错误样例：INVALID_BOOL ---------------------------------------

    def test_invalid_bool_uppercase_true_rejected(self):
        self.assert_rejected_on_fresh_and_seeded_db(
            ("dev", "new_ui", "TRUE"), "INVALID_BOOL"
        )

    def test_invalid_bool_one_rejected(self):
        self.assert_rejected_on_fresh_and_seeded_db(
            ("dev", "new_ui", "1"), "INVALID_BOOL"
        )

    def test_invalid_bool_empty_string_rejected(self):
        self.assert_rejected_on_fresh_and_seeded_db(
            ("dev", "new_ui", ""), "INVALID_BOOL"
        )

    # -- 错误样例：校验优先级 ------------------------------------------

    def test_error_priority_empty_env_before_key_and_value(self):
        # 环境、键和值同时不合法时先报告 EMPTY_ENV。
        self.assert_rejected_on_fresh_and_seeded_db(
            ("", "other_key", "TRUE"), "EMPTY_ENV"
        )

    def test_error_priority_unknown_key_before_invalid_bool(self):
        # 环境合法而键和值均不合法时报告 UNKNOWN_KEY。
        self.assert_rejected_on_fresh_and_seeded_db(
            ("dev", "other_key", "TRUE"), "UNKNOWN_KEY"
        )


if __name__ == "__main__":
    unittest.main()
