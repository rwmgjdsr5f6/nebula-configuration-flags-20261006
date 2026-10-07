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
* set --dry-run 只读预览单键变化：固定样例 demo.sqlite 中
  dev/new_ui=true、qa/new_ui=false，set dev new_ui false
  --dry-run 的 stdout 为单行
  {"new_ui":{"before":true,"after":false}} 加换行，退出 0、stderr
  为空，随后 get dev new_ui 仍输出 true；原值与目标值相同（含已
  保存 false）时预览为 {}，目标记录不存在、父目录存在而库文件缺失
  或有效库没有 flags 表时 before 为 null、after 为目标值，不把未
  设置当成 false，也不补建文件或表；父目录不存在、普通文本文件、
  flags 表缺 value 列或目标 new_ui 的存储文本不是严格 true/false
  时报 STORAGE_ERROR、保留原值、不输出部分结果，其他环境或未知键
  的异常值不影响合法目标预览；环境名两端空白被去除、内部空白保留、
  大小写敏感；每次预览前后库文件字节、完整记录与表结构均保持一致，
  重复预览结果相同，不带该选项的 set 仍写入并回显布尔文本；所有
  失败均退出 2、stdout 为空、stderr 仅为错误码加换行；
* unset --dry-run 只读预览撤销直接设置的变化：固定样例 demo.sqlite
  中 dev/new_ui=false、qa/new_ui=true，unset dev new_ui --dry-run
  的 stdout 为单行 {"new_ui":{"before":false,"after":null}} 加换行，
  退出 0、stderr 为空，随后 get dev new_ui 仍输出 false、qa 仍为
  true；原值 true 同样输出 before:true、after:null，false 不被视为
  未设置；父目录存在而库文件缺失、有效库缺 flags 表或目标行不存在时
  报 VALUE_NOT_SET；父目录不存在、普通文本文件、flags 表缺 value 列
  或目标行值不是严格文本 true/false 时报 STORAGE_ERROR、保留异常原值、
  不输出部分结果，其他环境或未知键的异常值不影响合法目标预览；环境名
  两端空白被去除、内部空白保留、大小写敏感，键名不修剪或转换大小写；
  空或全空白环境报 EMPTY_ENV 且优先于 UNKNOWN_KEY，输入校验先于存储
  访问；预览不接收布尔值参数，多给位置参数由 argparse 拒绝；每次预览
  前后库文件字节、完整记录与表结构均保持一致，重复预览结果相同，不带
  该选项的 unset 仍删除整行并输出 unset（原值非法也照常删除），重复
  删除报 VALUE_NOT_SET；所有失败均退出 2、stdout 为空、stderr 仅为
  错误码加换行；
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
  报 STORAGE_ERROR 且既有记录保持原样；空对象 {} 在父目录缺失与
  目标为普通文本文件两种异常存储路径下依旧退出 0、stdout 严格为
  {} 且不创建目录或改动文件字节，同一库路径上的非空导入对照报
  STORAGE_ERROR 且现场保持不变。
* import 非空导入在事务内写入失败时的回滚与恢复重试：固定样例
  demo.sqlite（标准 flags 表，仅 dev/new_ui=true 与 qa/new_ui=true）
  导入 {"new_ui": false}，目标行已在事务内写成 false 而尚未提交时
  发生 SQLite 写入错误（注入点自证该时序并留下可核对证据），退出 2、
  stdout 为空、stderr 仅为 STORAGE_ERROR 加换行；重新打开数据库后
  dev 与 qa 均仍为 true，完整记录与表结构不变，导入文件内容不变；
  解除失败条件后以同一环境同一文件立即重试，退出 0、stderr 为空、
  stdout 为单行 {"new_ui": false} 加换行，dev 变为 false、qa 仍为
  true，完整记录仅这两条。
* import --dry-run 只读预览非空导入的差异：固定样例 demo.sqlite 中
  dev/new_ui=true、qa/new_ui=false，candidate.json 为内容
  {"new_ui":false} 的 UTF-8 文件，import dev candidate.json
  --dry-run 的 stdout 为单行
  {"new_ui":{"before":true,"after":false}} 加换行，退出 0、stderr
  为空，随后 get dev new_ui 仍输出 true；原值已是 false 时预览为
  {}，目标记录不存在、父目录存在而库文件缺失或有效库没有 flags 表
  时 before 为 null、after 为 false，不把未设置当成 false，也不补
  建文件或表；合法空对象 {} 不访问存储，库路径父目录不存在时仍
  输出 {} 且不创建目录，相同路径改用非空文件则报 STORAGE_ERROR；
  目标 new_ui 的存储文本为 yes 时报 STORAGE_ERROR、保留原值、不
  输出部分结果，其他环境或未知键的异常值不影响合法目标预览；每次
  预览前后输入文件字节、库文件字节、完整记录与表结构均保持一致，
  qa 记录保留；所有失败均退出 2、stdout 为空、stderr 仅为错误码
  加换行，验收同时核对输出内容与调用后的文件状态。
* import --dry-run 的输入拒绝顺序与文件保护：库路径父目录不存在时，
  纯空白环境名搭配不存在的输入文件只报 EMPTY_ENV，合法环境名搭配
  不存在的文件只报 IMPORT_READ_ERROR，{"other_key":NaN} 只报
  INVALID_JSON（非标准 JSON 常量的拒绝先于键名校验），
  {"new_ui":"false","other_key":true} 只报 UNKNOWN_KEY（全部键名
  的检查先于布尔值检查），仅含 {"new_ui":"false"} 只报 INVALID_BOOL；
  输入错误均先于存储访问，不被 STORAGE_ERROR 取代，每次失败退出 2、
  stdout 为空、stderr 仅为错误码加换行，缺失的父目录与库文件仍不
  存在，已有输入文件字节保持原样；并在同一有效 demo.sqlite
  （dev/new_ui=true、qa/new_ui=false）上连续对照：先以
  {"new_ui":"false"} 预览报 INVALID_BOOL，改为 {"new_ui":false}
  后再预览输出 {"new_ui":{"before":true,"after":false}}，两次调用
  均不改变数据库记录、表结构、文件字节与输入文件，预览后 dev 仍为
  true、qa 仍为 false。
* import 与 import --dry-run 对重复 JSON 键的一致拒绝：固定样例
  demo.sqlite 只保存 dev/new_ui=true 与 qa/new_ui=false，
  candidate.json 为 UTF-8 文本；{"new_ui":true,"\\u006eew_ui":false}
  的转义解码后键名相同，{"new_ui":{"x":1,"x":2}} 与
  {"other_key":{"x":1,"x":2}} 的嵌套重复键先于布尔值与未知键
  校验，两种导入方式均报 INVALID_JSON；对照样例
  {"new_ui":[{"x":1},{"x":2}]} 中不同对象各自出现一次 x 不构成
  重复，数组不是合法开关值，均报 INVALID_BOOL。每次失败退出 2、
  stdout 为空、stderr 仅为错误码加换行；各用例独立准备输入，调用
  后 candidate.json 字节、库文件字节与全部记录保持原样，dev 与
  qa 不被覆盖；父目录存在而库文件缺失时拒绝结果相同且不创建库
  文件。
* export 把单环境已保存的直接设置导出为 JSON 文件：固定样例
  dev/new_ui=false、qa/new_ui=true 导出 dev 得到 {"new_ui": false}，
  stdout 与文件同为单行 JSON 加换行（无 BOM），再导入另一库的
  review 环境后 review/new_ui=false 且源库保持原值；环境名 ->
  读取配置 -> 输出文件的处理顺序（EMPTY_ENV / EXPORT_WRITE_ERROR /
  EXPORT_EXISTS）；库文件缺失、有效库缺 flags 表或环境无设置时
  导出 {} 且不创建数据库或表；父目录缺失、普通文本文件、flags 表
  缺列或目标环境合法键存有非法值时报 STORAGE_ERROR，不创建或改动
  输出文件；未知键与其他环境的异常值不影响导出；输出路径与库路径
  相同报 EXPORT_WRITE_ERROR，输出已存在报 EXPORT_EXISTS 且原内容
  保留，输出父目录缺失报 EXPORT_WRITE_ERROR 且不创建目录；成功与
  失败路径下源库字节均保持不变。
* export 处理顺序（环境名 -> 完整读取配置 -> 输出文件）的组合回归：
  固定样例 demo.sqlite（flags 表仅 dev/new_ui=yes 与 qa/new_ui=true）
  与预先保存 UTF-8 文本 KEEP 的 out.json 同处一个已存在的临时目录，
  多项错误同时存在时结果唯一——空字符串或仅含空格、制表符的环境名
  只报 EMPTY_ENV；环境为 dev 时只报 STORAGE_ERROR，已存在的
  out.json 不使结果变为 EXPORT_EXISTS；输出路径为 demo.sqlite
  本身（含带 ./ 的等价写法）时仍只报 STORAGE_ERROR，不先报
  EXPORT_WRITE_ERROR。每次失败退出 2、stdout 为空、stderr 严格为
  错误码加换行；调用后源库字节与全部记录（yes 不被修正、true 原样
  保留）、out.json 的 KEEP 与临时目录文件清单均不变，每个用例独立
  准备样例。
* export 在文件创建之后写入失败的文件保护：固定样例库 demo.sqlite
  （dev/new_ui=false、qa/new_ui=true）导出 dev 到起初不存在的
  dev.json，文件已创建但尚未写入内容与已写入部分内容后两种 OSError
  场景均退出 2、stdout 为空、stderr 仅为 EXPORT_WRITE_ERROR 加换行，
  调用结束后 dev.json 不残留，源库全部记录与文件字节、临时目录内
  其他文件保持原样；注入点自身校验失败确实发生在文件创建之后
  （部分写入场景确实产生过非空内容）；恢复正常写入条件后以相同
  环境与输出路径立即重试，退出 0、stderr 为空，stdout 与生成文件
  同为单行 {"new_ui": false} 加换行（无 BOM 的 UTF-8），qa 仍为
  true，源库保持原样。

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

    def set_preview(self, db, env, key, value):
        """在全新进程中执行 set <env> <key> <value> --dry-run。"""
        return self.run_flagctl(db, "set", env, key, value, "--dry-run")

    def get_flag(self, db, env, key):
        return self.run_flagctl(db, "get", env, key)

    def unset_flag(self, db, env, key):
        return self.run_flagctl(db, "unset", env, key)

    def unset_preview(self, db, env, key):
        """在全新进程中执行 unset <env> <key> --dry-run。"""
        return self.run_flagctl(db, "unset", env, key, "--dry-run")

    def list_flags(self, db, env):
        return self.run_flagctl(db, "list", env)

    def diff_flags(self, db, left, right):
        return self.run_flagctl(db, "diff", left, right)

    def envs_flags(self, db):
        return self.run_flagctl(db, "envs")

    def import_flags(self, db, env, file_path):
        return self.run_flagctl(db, "import", env, file_path)

    def preview_flags(self, db, env, file_path):
        """在全新进程中执行 import <env> <file> --dry-run。"""
        return self.run_flagctl(db, "import", env, file_path, "--dry-run")

    def export_flags(self, db, env, file_path):
        return self.run_flagctl(db, "export", env, file_path)

    def write_import_file(self, content, name="settings.json", raw=False):
        """在临时目录写入导入文件并返回路径；raw=True 时 content 为字节。"""
        path = os.path.join(self.tmpdir, name)
        mode = "wb" if raw else "w"
        with open(path, mode, **({} if raw else {"encoding": "utf-8"})) as fh:
            fh.write(content)
        return path

    def read_file_bytes(self, path):
        """读取文件的完整字节内容。"""
        with open(path, "rb") as fh:
            return fh.read()

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


class TestUnsetDryRunPreview(FlagctlCliTestCase):
    """unset --dry-run 只读预览撤销变化的命令行回归测试。

    主样例固定为 demo.sqlite 中 dev/new_ui=false、qa/new_ui=true：
    unset dev new_ui --dry-run 的 stdout 为单行
    {"new_ui":{"before":false,"after":null}} 加换行，退出 0、stderr
    为空，随后 get dev new_ui 仍输出 false，qa 仍为 true。另覆盖原值
    true 同样输出变化（false 不被视为未设置）、目标行缺失与库文件
    缺失/缺 flags 表（报 VALUE_NOT_SET，不补建文件或表）、目标值损坏
    报 STORAGE_ERROR 且不输出部分结果、其他环境与未知键记录不影响预览、
    父目录缺失/普通文本文件/缺 value 列报 STORAGE_ERROR、环境名区分
    大小写并保留内部空白、重复预览结果相同，以及不带 --dry-run 的
    unset 仍删除整行并回显 unset（原值非法也照常删除），重复删除报
    VALUE_NOT_SET。

    每次预览前后都核对库文件字节、表结构与完整记录；失败一律
    退出 2、stdout 为空、stderr 仅为错误码加换行。
    """

    def make_demo_db(self):
        """固定主样例库 demo.sqlite：dev/new_ui=false、qa/new_ui=true。"""
        db = os.path.join(self.tmpdir, "demo.sqlite")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )
        return db

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

    def create_db_without_flags_table(self, db):
        """创建只含无关表 notes（含一行数据）的有效 SQLite 库。"""
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO notes (text) VALUES ('sample note')")
        finally:
            conn.close()

    def read_notes_rows(self, db):
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT id, text FROM notes ORDER BY id").fetchall()
        finally:
            conn.close()

    def snapshot_storage(self, db):
        """采集库文件存在性、字节、表清单及 flags 表结构与完整记录。"""
        snapshot = {"exists": os.path.exists(db)}
        if snapshot["exists"]:
            snapshot["bytes"] = self.read_file_bytes(db)
            conn = sqlite3.connect(db)
            try:
                tables = [
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "ORDER BY name"
                    ).fetchall()
                ]
                snapshot["tables"] = tables
                if "flags" in tables:
                    snapshot["columns"] = [
                        row[1]
                        for row in conn.execute("PRAGMA table_info(flags)").fetchall()
                    ]
                    snapshot["rows"] = conn.execute(
                        "SELECT env, key, value FROM flags ORDER BY env, key"
                    ).fetchall()
                else:
                    snapshot["columns"] = None
                    snapshot["rows"] = None
            finally:
                conn.close()
        return snapshot

    def assert_storage_snapshot_unchanged(self, db, before):
        """调用后的存储现场（文件字节、表结构、完整记录）必须与快照一致。"""
        self.assertEqual(self.snapshot_storage(db), before)

    def test_fixed_sample_previews_unset_without_deleting(self):
        # 主验收样例：dev/new_ui=false 预览撤销，stdout 严格为单行
        # {"new_ui":{"before":false,"after":null}} 加换行；预览不删除，
        # get dev new_ui 仍输出 false，qa 的 true 保留。
        db = self.make_demo_db()

        before = self.snapshot_storage(db)
        proc = self.unset_preview(db, "dev", "new_ui")

        self.assertCommandOk(
            proc, '{"new_ui":{"before":false,"after":null}}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )
        self.assertEqual(self.read_table_names(db), ["flags"])
        self.assertEqual(self.read_flags_columns(db), ["env", "key", "value"])

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_true_value_previews_change_with_after_null(self):
        # 原值为 true 同样输出变化：before 为 true、after 为 null；false
        # 与 true 都是已保存设置，预览不把任一方当作未设置。
        db = self.make_demo_db()

        before = self.snapshot_storage(db)
        proc = self.unset_preview(db, "qa", "new_ui")

        self.assertCommandOk(proc, '{"new_ui":{"before":true,"after":null}}')
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_repeated_previews_are_identical_and_never_delete(self):
        # 连续两次预览结果逐字节相同，库现场保持不变；预览不会因重复
        # 调用而删除记录。
        db = self.make_demo_db()
        before = self.snapshot_storage(db)

        for _ in range(2):
            proc = self.unset_preview(db, "dev", "new_ui")
            self.assertCommandOk(
                proc, '{"new_ui":{"before":false,"after":null}}'
            )
            self.assert_storage_snapshot_unchanged(db, before)

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")

    def test_missing_row_reports_value_not_set_without_creating_row(self):
        # flags 表只有 qa/new_ui=true：预览撤销 dev 报 VALUE_NOT_SET，
        # qa 记录保持 true，不新增 dev 记录，也不输出部分结果。
        db = self.db_path("unset_preview_only_qa_row")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "true")])

        before = self.snapshot_storage(db)
        proc = self.unset_preview(db, "dev", "new_ui")

        self.assertCommandError(proc, "VALUE_NOT_SET")
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "true")])
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_missing_db_file_reports_value_not_set_without_creating_file(self):
        # 父目录存在而库文件缺失：报 VALUE_NOT_SET，退出 2，不补建库
        # 文件，临时目录内文件清单不变。
        db = self.db_path("unset_preview_missing_db")
        self.assertFalse(os.path.exists(db))
        listing_before = sorted(os.listdir(self.tmpdir))

        proc = self.unset_preview(db, "dev", "new_ui")

        self.assertCommandError(proc, "VALUE_NOT_SET")
        self.assertFalse(os.path.exists(db), "预览不得补建数据库文件: %s" % db)
        self.assertEqual(
            sorted(os.listdir(self.tmpdir)),
            listing_before,
            "预览不得在临时目录内新增任何文件",
        )

    def test_valid_db_without_flags_table_reports_value_not_set_without_table(self):
        # 有效库没有 flags 表：报 VALUE_NOT_SET，不补建 flags 表，原有
        # 表与其数据、库文件字节均保持不变。
        db = self.db_path("unset_preview_no_flags_table")
        self.create_db_without_flags_table(db)
        self.assertEqual(self.read_table_names(db), ["notes"])

        before = self.snapshot_storage(db)
        proc = self.unset_preview(db, "dev", "new_ui")

        self.assertCommandError(proc, "VALUE_NOT_SET")
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(self.read_table_names(db), ["notes"])
        self.assertEqual(self.read_notes_rows(db), [(1, "sample note")])

    def test_corrupt_target_value_reports_storage_error_without_partial_output(self):
        # 目标 new_ui 的存储文本为 yes：报 STORAGE_ERROR，不输出部分
        # 结果，原值 yes 保留且不被修复，库文件字节、完整记录与表结构
        # 不变；qa 的合法 true 预览不受影响，仍输出 before:true。
        db = self.db_path("unset_preview_bad_target")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "true")],
        )

        before = self.snapshot_storage(db)
        proc = self.unset_preview(db, "dev", "new_ui")

        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "true")],
        )
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "STORAGE_ERROR")
        self.assertCommandOk(
            self.unset_preview(db, "qa", "new_ui"),
            '{"new_ui":{"before":true,"after":null}}',
        )

    def test_invalid_value_in_other_env_does_not_affect_preview(self):
        # 其他环境的异常值不在目标范围内：qa/new_ui=yes 不影响 dev 的
        # 合法预览，预览后异常原值原样保留。
        db = self.db_path("unset_preview_other_env_bad")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "false"), ("qa", "new_ui", "yes")],
        )

        before = self.snapshot_storage(db)
        proc = self.unset_preview(db, "dev", "new_ui")

        self.assertCommandOk(
            proc, '{"new_ui":{"before":false,"after":null}}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "yes")],
        )

    def test_unknown_key_rows_do_not_affect_preview(self):
        # 未知键（含其异常值，无论是否在目标环境内）一律忽略：合法目标
        # dev/new_ui=true 的预览正常输出，未知键异常行原样保留。
        db = self.db_path("unset_preview_unknown_key_bad")
        self.seed_flags_table(
            db,
            [
                ("dev", "new_ui", "true"),
                ("dev", "other_key", "yes"),
                ("qa", "other_key", "maybe"),
            ],
        )

        before = self.snapshot_storage(db)
        proc = self.unset_preview(db, "dev", "new_ui")

        self.assertCommandOk(
            proc, '{"new_ui":{"before":true,"after":null}}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [
                ("dev", "new_ui", "true"),
                ("dev", "other_key", "yes"),
                ("qa", "other_key", "maybe"),
            ],
        )

    def test_storage_error_states_keep_storage_untouched(self):
        # 父目录不存在、目标为普通文本文件、flags 表缺 value 列三种状态
        # 下，合法输入的预览都报 STORAGE_ERROR，不创建或改动任何对象。
        missing_dir = os.path.join(self.tmpdir, "unset_preview_no_such_dir")
        missing_db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        proc = self.unset_preview(missing_db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(missing_db), "不得创建数据库文件")

        text_db = self.db_path("unset_preview_plain_text")
        content = b"this is not a sqlite database\n"
        with open(text_db, "wb") as fh:
            fh.write(content)
        proc = self.unset_preview(text_db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assertEqual(self.read_file_bytes(text_db), content)

        no_column_db = self.db_path("unset_preview_no_value_column")
        conn = sqlite3.connect(no_column_db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key) VALUES (?, ?)",
                    ("dev", "new_ui"),
                )
        finally:
            conn.close()
        proc = self.unset_preview(no_column_db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assertEqual(self.read_flags_columns(no_column_db), ["env", "key"])
        self.assertEqual(
            self.read_flags_env_key_rows(no_column_db), [("dev", "new_ui")]
        )

    def test_env_name_case_and_internal_whitespace_are_preserved(self):
        # 环境名两端空白被去除、内部空白保留且区分大小写：" De v " 命中
        # 已保存的 "De v" 记录（before 为 false），与 "dev" 是两个不同
        # 环境（后者在样例中也是 false，但记录相互独立）；预览均不删除。
        db = self.make_demo_db()
        self.assertCommandOk(self.set_flag(db, "De v", "new_ui", "true"), "true")

        before = self.snapshot_storage(db)
        spaced = self.unset_preview(db, "  De v  ", "new_ui")
        self.assertCommandOk(
            spaced, '{"new_ui":{"before":true,"after":null}}'
        )
        lower = self.unset_preview(db, "dev", "new_ui")
        self.assertCommandOk(
            lower, '{"new_ui":{"before":false,"after":null}}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertCommandOk(self.get_flag(db, "De v", "new_ui"), "true")

    def test_preview_does_not_accept_a_bool_argument(self):
        # unset 不接收布尔值参数：多给一个位置参数由 argparse 拒绝，
        # 退出 2、stdout 为空、stderr 为用法错误（不触碰数据库）。
        db = self.make_demo_db()
        before = self.snapshot_storage(db)
        proc = self.run_flagctl(db, "unset", "dev", "new_ui", "false", "--dry-run")

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("unrecognized arguments", proc.stderr)
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")

    def test_normal_unset_still_deletes_row_and_echoes_unset(self):
        # 不带 --dry-run 的 unset 行为不变：删除整行并回显 unset，get
        # 随即得到 VALUE_NOT_SET；qa 不受影响。
        db = self.make_demo_db()

        removed = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandOk(removed, "unset")
        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "true")])
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_normal_unset_deletes_row_with_illegal_value(self):
        # 正式 unset 不做值校验：目标原值非法（yes）也照常删除整行。
        db = self.db_path("unset_preview_then_real_bad_value")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "true")],
        )

        removed = self.unset_flag(db, "dev", "new_ui")
        self.assertCommandOk(removed, "unset")
        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "true")])
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")

    def test_repeated_normal_unset_reports_value_not_set(self):
        # 预览之后正式 unset 仍然删除；对已删除的行重复 unset 报
        # VALUE_NOT_SET，全程不新增记录。
        db = self.make_demo_db()
        self.assertCommandOk(
            self.unset_preview(db, "qa", "new_ui"),
            '{"new_ui":{"before":true,"after":null}}',
        )

        self.assertCommandOk(self.unset_flag(db, "qa", "new_ui"), "unset")
        again = self.unset_flag(db, "qa", "new_ui")
        self.assertCommandError(again, "VALUE_NOT_SET")
        self.assertEqual(
            self.read_flags_rows(db), [("dev", "new_ui", "false")]
        )


class TestUnsetDryRunRejectionOrder(FlagctlCliTestCase):
    """unset --dry-run 的输入拒绝顺序回归。

    库路径的父目录始终不存在：输入错误必须严格按环境名 -> 键名的顺序
    先于任何存储访问报告（unset 没有布尔值参数），多个输入错误并存时
    只返回最先遇到的错误，不降级为 STORAGE_ERROR。每个失败调用退出 2、
    stdout 为空、stderr 仅为错误码加换行；调用后缺失的父目录与库文件
    仍不存在。输入全部合法时才读取存储，此时因父目录缺失报
    STORAGE_ERROR。
    """

    def missing_parent_db(self, label):
        """返回父目录与库文件均不存在的库路径及其父目录。"""
        missing_dir = os.path.join(self.tmpdir, label + "_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        self.assertFalse(os.path.exists(db))
        return missing_dir, db

    def assert_parent_still_missing(self, missing_dir, db):
        """调用后缺失的父目录与库文件必须仍然不存在。"""
        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_empty_env_takes_precedence_over_unknown_key(self):
        # 纯空白环境名搭配非法键名：只报 EMPTY_ENV。
        missing_dir, db = self.missing_parent_db("unset_dryrun_blank_env")
        proc = self.unset_preview(db, "   ", "bad_key")
        self.assertCommandError(proc, "EMPTY_ENV")
        self.assert_parent_still_missing(missing_dir, db)

    def test_unknown_key_reported_after_env(self):
        # 环境合法而键名非法：只报 UNKNOWN_KEY。
        missing_dir, db = self.missing_parent_db("unset_dryrun_unknown_key")
        proc = self.unset_preview(db, "dev", "other_key")
        self.assertCommandError(proc, "UNKNOWN_KEY")
        self.assert_parent_still_missing(missing_dir, db)

    def test_key_is_not_trimmed_or_case_folded(self):
        # 键名不修剪空白也不转换大小写：带空白或大写的 new_ui 都是
        # UNKNOWN_KEY，校验先于存储访问。
        missing_dir, db = self.missing_parent_db("unset_dryrun_key_shape")
        for key in (" new_ui", "new_ui ", " New_UI ", "NEW_UI"):
            with self.subTest(key=key):
                proc = self.unset_preview(db, "dev", key)
                self.assertCommandError(proc, "UNKNOWN_KEY")
                self.assert_parent_still_missing(missing_dir, db)

    def test_unknown_key_rejected_keeps_saved_rows(self):
        # 已有记录的库上预览撤销未知键：报 UNKNOWN_KEY，既有记录全部
        # 保持原样，不删除任何行。
        db = self.db_path("unset_dryrun_kept_rows")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")

        proc = self.unset_preview(db, "dev", "other_key")
        self.assertCommandError(proc, "UNKNOWN_KEY")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")

    def test_valid_input_reaches_storage_and_reports_storage_error(self):
        # 环境与键全部合法后才读取存储：父目录不存在此时报
        # STORAGE_ERROR，证明输入校验确实全部通过。
        missing_dir, db = self.missing_parent_db("unset_dryrun_storage")
        proc = self.unset_preview(db, "dev", "new_ui")
        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assert_parent_still_missing(missing_dir, db)


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


class TestSetDryRunPreview(FlagctlCliTestCase):
    """set --dry-run 只读预览单键变化的命令行回归测试。

    主样例固定为 demo.sqlite 中 dev/new_ui=true、qa/new_ui=false：
    set dev new_ui false --dry-run 的 stdout 为单行
    {"new_ui":{"before":true,"after":false}} 加换行，退出 0、stderr
    为空，随后 get dev new_ui 仍输出 true。另覆盖原值与目标值相同
    （含已保存 false，预览 {}）、目标记录缺失与库文件缺失/缺 flags
    表（before 为 null，未设置不被当成 false，不补建文件或表）、
    目标值损坏报 STORAGE_ERROR 且不输出部分结果、其他环境与未知键
    记录不影响预览、父目录缺失/普通文本文件/缺 value 列报
    STORAGE_ERROR、环境名区分大小写并保留内部空白、重复预览结果
    相同，以及不带 --dry-run 的 set 仍写入并回显布尔文本。

    每次预览前后都核对库文件字节、表结构与完整记录；失败一律
    退出 2、stdout 为空、stderr 仅为错误码加换行。
    """

    def make_demo_db(self):
        """固定主样例库 demo.sqlite：dev/new_ui=true、qa/new_ui=false。"""
        db = os.path.join(self.tmpdir, "demo.sqlite")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "false")],
        )
        return db

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

    def create_db_without_flags_table(self, db):
        """创建只含无关表 notes（含一行数据）的有效 SQLite 库。"""
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO notes (text) VALUES ('sample note')")
        finally:
            conn.close()

    def read_notes_rows(self, db):
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT id, text FROM notes ORDER BY id").fetchall()
        finally:
            conn.close()

    def snapshot_storage(self, db):
        """采集库文件存在性、字节、表清单及 flags 表结构与完整记录。"""
        snapshot = {"exists": os.path.exists(db)}
        if snapshot["exists"]:
            snapshot["bytes"] = self.read_file_bytes(db)
            conn = sqlite3.connect(db)
            try:
                tables = [
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "ORDER BY name"
                    ).fetchall()
                ]
                snapshot["tables"] = tables
                if "flags" in tables:
                    snapshot["columns"] = [
                        row[1]
                        for row in conn.execute("PRAGMA table_info(flags)").fetchall()
                    ]
                    snapshot["rows"] = conn.execute(
                        "SELECT env, key, value FROM flags ORDER BY env, key"
                    ).fetchall()
                else:
                    snapshot["columns"] = None
                    snapshot["rows"] = None
            finally:
                conn.close()
        return snapshot

    def assert_storage_snapshot_unchanged(self, db, before):
        """调用后的存储现场（文件字节、表结构、完整记录）必须与快照一致。"""
        self.assertEqual(self.snapshot_storage(db), before)

    def test_fixed_sample_previews_change_without_writing(self):
        # 主验收样例：dev/new_ui=true 预览设为 false，stdout 严格为单行
        # {"new_ui":{"before":true,"after":false}} 加换行；预览不写入，
        # get dev new_ui 仍输出 true，qa 的 false 保留。
        db = self.make_demo_db()

        before = self.snapshot_storage(db)
        proc = self.set_preview(db, "dev", "new_ui", "false")

        self.assertCommandOk(
            proc, '{"new_ui":{"before":true,"after":false}}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "false")],
        )
        self.assertEqual(self.read_table_names(db), ["flags"])
        self.assertEqual(self.read_flags_columns(db), ["env", "key", "value"])

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_same_value_previews_empty_object(self):
        # 原值与目标值相同（true->true 与 false->false）时没有变化：
        # 输出 {}，库现场保持不变；false 是已保存设置而非未设置。
        db = self.make_demo_db()

        before = self.snapshot_storage(db)
        same_true = self.set_preview(db, "dev", "new_ui", "true")
        self.assertCommandOk(same_true, "{}")
        self.assert_storage_snapshot_unchanged(db, before)

        same_false = self.set_preview(db, "qa", "new_ui", "false")
        self.assertCommandOk(same_false, "{}")
        self.assert_storage_snapshot_unchanged(db, before)

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_false_target_on_unset_key_previews_null_before(self):
        # 目标记录缺失时把 false 作为目标值：before 必须是 null 而不是
        # false，after 为 false；预览后 dev 仍无记录，不新增行。重复
        # 预览结果相同，qa 的 false 保留。
        db = self.db_path("set_preview_null_row")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "false")])

        before = self.snapshot_storage(db)
        first = self.set_preview(db, "dev", "new_ui", "false")
        second = self.set_preview(db, "dev", "new_ui", "false")

        self.assertCommandOk(first, '{"new_ui":{"before":null,"after":false}}')
        self.assertCommandOk(second, '{"new_ui":{"before":null,"after":false}}')
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "false")])
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_missing_db_file_previews_null_without_creating_file(self):
        # 父目录存在而库文件缺失：before 为 null，退出 0，不补建库文件，
        # 临时目录内文件清单不变。
        db = self.db_path("set_preview_missing_db")
        self.assertFalse(os.path.exists(db))
        listing_before = sorted(os.listdir(self.tmpdir))

        proc = self.set_preview(db, "dev", "new_ui", "false")

        self.assertCommandOk(proc, '{"new_ui":{"before":null,"after":false}}')
        self.assertFalse(os.path.exists(db), "预览不得补建数据库文件: %s" % db)
        self.assertEqual(
            sorted(os.listdir(self.tmpdir)),
            listing_before,
            "预览不得在临时目录内新增任何文件",
        )

    def test_valid_db_without_flags_table_previews_null_without_creating_table(self):
        # 有效库没有 flags 表：before 为 null，不补建 flags 表，原有表与
        # 其数据、库文件字节均保持不变。
        db = self.db_path("set_preview_no_flags_table")
        self.create_db_without_flags_table(db)
        self.assertEqual(self.read_table_names(db), ["notes"])

        before = self.snapshot_storage(db)
        proc = self.set_preview(db, "dev", "new_ui", "false")

        self.assertCommandOk(proc, '{"new_ui":{"before":null,"after":false}}')
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(self.read_table_names(db), ["notes"])
        self.assertEqual(self.read_notes_rows(db), [(1, "sample note")])

    def test_corrupt_target_value_reports_storage_error_without_partial_output(self):
        # 目标 new_ui 的存储文本为 yes：报 STORAGE_ERROR，不输出部分
        # 结果，原值 yes 保留，库文件字节、完整记录与表结构不变；qa 的
        # 合法 false 预览不受影响。
        db = self.db_path("set_preview_bad_target")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")],
        )

        before = self.snapshot_storage(db)
        proc = self.set_preview(db, "dev", "new_ui", "false")

        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")],
        )
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "STORAGE_ERROR")
        self.assertCommandOk(self.set_preview(db, "qa", "new_ui", "false"), "{}")

    def test_invalid_value_in_other_env_does_not_affect_preview(self):
        # 其他环境的异常值不在目标范围内：qa/new_ui=yes 不影响 dev 的
        # 合法预览，预览后异常原值原样保留。
        db = self.db_path("set_preview_other_env_bad")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "true"), ("qa", "new_ui", "yes")],
        )

        before = self.snapshot_storage(db)
        proc = self.set_preview(db, "dev", "new_ui", "false")

        self.assertCommandOk(
            proc, '{"new_ui":{"before":true,"after":false}}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "yes")],
        )

    def test_unknown_key_rows_do_not_affect_preview(self):
        # 未知键（含其异常值，无论是否在目标环境内）一律忽略：合法目标
        # dev/new_ui=true 的预览正常输出差异，未知键异常行原样保留。
        db = self.db_path("set_preview_unknown_key_bad")
        self.seed_flags_table(
            db,
            [
                ("dev", "new_ui", "true"),
                ("dev", "other_key", "yes"),
                ("qa", "other_key", "maybe"),
            ],
        )

        before = self.snapshot_storage(db)
        proc = self.set_preview(db, "dev", "new_ui", "false")

        self.assertCommandOk(
            proc, '{"new_ui":{"before":true,"after":false}}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [
                ("dev", "new_ui", "true"),
                ("dev", "other_key", "yes"),
                ("qa", "other_key", "maybe"),
            ],
        )

    def test_storage_error_states_keep_storage_untouched(self):
        # 父目录不存在、目标为普通文本文件、flags 表缺 value 列三种状态
        # 下，合法输入的预览都报 STORAGE_ERROR，不创建或改动任何对象。
        missing_dir = os.path.join(self.tmpdir, "set_preview_no_such_dir")
        missing_db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        proc = self.set_preview(missing_db, "dev", "new_ui", "false")
        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(missing_db), "不得创建数据库文件")

        text_db = self.db_path("set_preview_plain_text")
        content = b"this is not a sqlite database\n"
        with open(text_db, "wb") as fh:
            fh.write(content)
        proc = self.set_preview(text_db, "dev", "new_ui", "false")
        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assertEqual(self.read_file_bytes(text_db), content)

        no_column_db = self.db_path("set_preview_no_value_column")
        conn = sqlite3.connect(no_column_db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags (env, key) VALUES (?, ?)",
                    ("dev", "new_ui"),
                )
        finally:
            conn.close()
        proc = self.set_preview(no_column_db, "dev", "new_ui", "false")
        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assertEqual(self.read_flags_columns(no_column_db), ["env", "key"])
        self.assertEqual(
            self.read_flags_env_key_rows(no_column_db), [("dev", "new_ui")]
        )

    def test_env_name_case_and_internal_whitespace_are_preserved(self):
        # 环境名两端空白被去除、内部空白保留且区分大小写：" De v " 命中
        # 已保存的 "De v" 记录，与 "dev" 是两个不同环境；预览均不写入。
        db = self.make_demo_db()
        self.assertCommandOk(self.set_flag(db, "De v", "new_ui", "false"), "false")

        before = self.snapshot_storage(db)
        spaced = self.set_preview(db, "  De v  ", "new_ui", "true")
        self.assertCommandOk(
            spaced, '{"new_ui":{"before":false,"after":true}}'
        )
        lower = self.set_preview(db, "dev", "new_ui", "false")
        self.assertCommandOk(
            lower, '{"new_ui":{"before":true,"after":false}}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertCommandOk(self.get_flag(db, "De v", "new_ui"), "false")

    def test_repeated_previews_are_identical_and_never_write(self):
        # 连续两次预览结果逐字节相同，库现场保持不变；预览不会因重复
        # 调用而落库。
        db = self.make_demo_db()
        before = self.snapshot_storage(db)

        for _ in range(2):
            proc = self.set_preview(db, "dev", "new_ui", "false")
            self.assertCommandOk(
                proc, '{"new_ui":{"before":true,"after":false}}'
            )
            self.assert_storage_snapshot_unchanged(db, before)

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")

    def test_normal_set_still_writes_and_echoes_bool_text(self):
        # 不带 --dry-run 的 set 行为不变：回显布尔文本并真正覆盖旧值，
        # get 随即读到新值；同库随后预览反映已落库的新值。
        db = self.make_demo_db()

        written = self.set_flag(db, "dev", "new_ui", "false")
        self.assertCommandOk(written, "false")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "false")],
        )
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")

        # 旧值已落库为 false，再预览设为 false 因此没有变化（{}）。
        self.assertCommandOk(self.set_preview(db, "dev", "new_ui", "false"), "{}")


class TestSetDryRunRejectionOrder(FlagctlCliTestCase):
    """set --dry-run 的输入拒绝顺序回归。

    库路径的父目录始终不存在：输入错误必须严格按环境名 -> 键名 ->
    布尔文本的顺序先于任何存储访问报告，多个输入错误并存时只返回
    最先遇到的错误，不降级为 STORAGE_ERROR。每个失败调用退出 2、
    stdout 为空、stderr 仅为错误码加换行；调用后缺失的父目录与库
    文件仍不存在。输入全部合法时才读取存储，此时因父目录缺失报
    STORAGE_ERROR。
    """

    def missing_parent_db(self, label):
        """返回父目录与库文件均不存在的库路径及其父目录。"""
        missing_dir = os.path.join(self.tmpdir, label + "_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        self.assertFalse(os.path.exists(db))
        return missing_dir, db

    def assert_parent_still_missing(self, missing_dir, db):
        """调用后缺失的父目录与库文件必须仍然不存在。"""
        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_empty_env_takes_precedence_over_key_and_bool(self):
        # 纯空白环境名搭配非法键名与非法布尔文本：只报 EMPTY_ENV。
        missing_dir, db = self.missing_parent_db("set_dryrun_blank_env")
        proc = self.set_preview(db, "   ", "bad_key", "TRUE")
        self.assertCommandError(proc, "EMPTY_ENV")
        self.assert_parent_still_missing(missing_dir, db)

    def test_unknown_key_takes_precedence_over_bool(self):
        # 环境合法而键名、布尔文本均非法：只报 UNKNOWN_KEY。
        missing_dir, db = self.missing_parent_db("set_dryrun_unknown_key")
        proc = self.set_preview(db, "dev", "other_key", "TRUE")
        self.assertCommandError(proc, "UNKNOWN_KEY")
        self.assert_parent_still_missing(missing_dir, db)

    def test_invalid_bool_reported_last_among_input_errors(self):
        # 环境与键合法、布尔文本非法：只报 INVALID_BOOL（严格小写
        # true/false 之外的文本一律拒绝）。
        missing_dir, db = self.missing_parent_db("set_dryrun_bad_bool")
        for value in ("TRUE", "False", "1", "yes", "", " true", "true "):
            with self.subTest(value=value):
                proc = self.set_preview(db, "dev", "new_ui", value)
                self.assertCommandError(proc, "INVALID_BOOL")
                self.assert_parent_still_missing(missing_dir, db)

    def test_valid_input_reaches_storage_and_reports_storage_error(self):
        # 三项输入全部合法后才读取存储：父目录不存在此时报
        # STORAGE_ERROR，证明输入校验确实全部通过。
        missing_dir, db = self.missing_parent_db("set_dryrun_storage")
        proc = self.set_preview(db, "dev", "new_ui", "false")
        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assert_parent_still_missing(missing_dir, db)


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


class TestFixedSampleReadValidation(FlagctlCliTestCase):
    """固定样例 demo.sqlite 下统一读取校验的端到端回归。

    flags 表仅保存 dev/new_ui=false 与 qa/new_ui=yes 两行（yes 绕过
    set 的输入校验直接落库，模拟存储中已存在的异常值）。重构后三处
    入口共用同一份已保存布尔值校验规则，本类固定：

    * get dev new_ui 只检查目标记录，输出 false；
    * list dev 只检查目标环境，输出 {"new_ui": false}，qa 的异常值
      在范围外；
    * envs 检查全库已知键，qa 的 yes 令其报 STORAGE_ERROR，dev 的
      false 不产生部分结果；
    * get/list 直接读 qa、diff 以 qa 为任一侧（间接复用 list 的
      读取校验）同样报 STORAGE_ERROR，左右顺序不影响结论；
    * 所有读取前后两行记录逐行不变，且读取不产生修复或写入。
    """

    def seed_demo_db(self, label):
        """建立固定样例库，返回其路径：仅 dev=false、qa=new_ui=yes 两行。"""
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
                    "INSERT INTO flags (env, key, value) VALUES (?, ?, ?)",
                    [("dev", "new_ui", "false"), ("qa", "new_ui", "yes")],
                )
        finally:
            conn.close()
        return db

    def test_get_dev_outputs_false(self):
        # get dev new_ui：退出 0，stdout 严格为 false 加换行，stderr 为空。
        db = self.seed_demo_db("demo_get")
        proc = self.get_flag(db, "dev", "new_ui")
        self.assertCommandOk(proc, "false")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "yes")],
        )

    def test_envs_reports_storage_error_and_rows_unchanged(self):
        # envs：退出 2，stdout 为空，stderr 仅 STORAGE_ERROR 加换行；
        # 执行前后记录（含 qa 的非法原值 yes）逐行保持不变。
        db = self.seed_demo_db("demo_envs")
        before = self.read_flags_rows(db)

        proc = self.envs_flags(db)
        self.assertCommandError(proc, "STORAGE_ERROR")

        self.assertEqual(self.read_flags_rows(db), before)

    def test_get_then_envs_on_same_db_both_match_fixed_sample(self):
        # 固定验收流程：同一个 demo.sqlite 上先 get dev new_ui 输出
        # false，再执行 envs 返回 STORAGE_ERROR；两次读取前后记录不变。
        db = self.seed_demo_db("demo_flow")
        before = self.read_flags_rows(db)

        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")
        self.assertEqual(self.read_flags_rows(db), before)

        self.assertCommandError(self.envs_flags(db), "STORAGE_ERROR")
        self.assertEqual(self.read_flags_rows(db), before)

    def test_list_dev_ok_while_list_qa_errors(self):
        # list 只检查目标环境的已知键：dev 输出 {"new_ui": false}，
        # qa 范围外的异常值不影响它；直接 list qa 则报 STORAGE_ERROR。
        db = self.seed_demo_db("demo_list")

        proc = self.list_flags(db, "dev")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {"new_ui": False})

        self.assertCommandError(self.list_flags(db, "qa"), "STORAGE_ERROR")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "yes")],
        )

    def test_get_qa_errors_and_diff_with_qa_on_either_side_errors(self):
        # 直接读 qa 的非法记录报 STORAGE_ERROR；diff 间接复用 list 的
        # 读取校验，qa 在左或在右都报 STORAGE_ERROR，不输出部分差异，
        # 记录原样保留。
        db = self.seed_demo_db("demo_diff")

        self.assertCommandError(self.get_flag(db, "qa", "new_ui"), "STORAGE_ERROR")
        self.assertCommandError(self.diff_flags(db, "dev", "qa"), "STORAGE_ERROR")
        self.assertCommandError(self.diff_flags(db, "qa", "dev"), "STORAGE_ERROR")

        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "yes")],
        )


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


class TestImportEmptyObjectAbnormalStorage(FlagctlCliTestCase):
    """空对象 {} 导入在异常存储路径下的成功/失败对照回归。

    空对象成功的前提只是环境名与输入文件通过既有校验，之后直接
    返回 {}，全程不访问数据库：因此无论存储路径处于什么异常状态
    都必须退出 0、stdout 严格为 ``{}\\n``、stderr 为空，且不创建
    任何目录或文件、不改动已有文件字节。同一路径上的非空导入
    （{"new_ui": false}）必须真正访问存储并报 STORAGE_ERROR，
    退出 2、stdout 为空、stderr 严格为 ``STORAGE_ERROR\\n``，
    现场与调用前一致。两组固定样例：

    * missing：目录 missing 及其中的 flags.sqlite 均不存在；
    * broken：broken.sqlite 已存在但内容是固定文本 local demo
      （10 字节，不是 SQLite 数据库）。
    """

    EMPTY_JSON = b"{}"
    NONEMPTY_JSON = b'{"new_ui":false}'
    BROKEN_TEXT = b"local demo"

    def write_named_file(self, name, content):
        """在临时目录按固定文件名写入 UTF-8 字节，返回完整路径。"""
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    def assert_import_empty_ok_and_untouched(self, db, state, empty_path):
        """import dev empty.json 成功输出 {}，且存储现场保持不变。"""
        proc = self.import_flags(db, "dev", empty_path)
        self.assertEqual(
            proc.returncode, 0,
            "%s: 空对象导入应退出 0，实际 stderr: %r" % (state, proc.stderr),
        )
        self.assertEqual(
            proc.stdout, "{}\n",
            "%s: 空对象导入 stdout 应为 {}\\n，实际: %r" % (state, proc.stdout),
        )
        self.assertEqual(
            proc.stderr, "",
            "%s: 空对象导入 stderr 应为空，实际: %r" % (state, proc.stderr),
        )

    def assert_import_nonempty_error_and_untouched(self, db, state, nonempty_path):
        """import dev nonempty.json 报 STORAGE_ERROR，stdout 为空，现场不变。"""
        proc = self.import_flags(db, "dev", nonempty_path)
        self.assertEqual(
            proc.returncode, 2,
            "%s: 非空导入应退出 2，实际 stdout: %r stderr: %r"
            % (state, proc.stdout, proc.stderr),
        )
        self.assertEqual(
            proc.stdout, "",
            "%s: 非空导入 stdout 应为空，实际: %r" % (state, proc.stdout),
        )
        self.assertEqual(
            proc.stderr, "STORAGE_ERROR\n",
            "%s: 非空导入 stderr 应为 STORAGE_ERROR\\n，实际: %r"
            % (state, proc.stderr),
        )

    def test_missing_directory_empty_succeeds_nonempty_errors(self):
        # 第一组：missing 目录及 flags.sqlite 均不存在。
        # 空对象导入退出 0、输出 {}\n、stderr 为空，目录与数据库
        # 仍不存在；随后非空导入报 STORAGE_ERROR，目录与数据库
        # 仍不存在（空对象的成功不代表非空导入也可写）。
        empty_path = self.write_named_file("empty.json", self.EMPTY_JSON)
        nonempty_path = self.write_named_file("nonempty.json", self.NONEMPTY_JSON)

        missing_dir = os.path.join(self.tmpdir, "missing")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        self.assertFalse(os.path.exists(db))

        self.assert_import_empty_ok_and_untouched(
            db, "missing 目录缺失", empty_path
        )
        self.assertFalse(
            os.path.exists(missing_dir),
            "missing: 空对象导入不得创建缺失的父目录",
        )
        self.assertFalse(
            os.path.exists(db), "missing: 空对象导入不得创建数据库文件"
        )

        self.assert_import_nonempty_error_and_untouched(
            db, "missing 目录缺失", nonempty_path
        )
        self.assertFalse(
            os.path.exists(missing_dir),
            "missing: 非空导入失败不得创建缺失的父目录",
        )
        self.assertFalse(
            os.path.exists(db), "missing: 非空导入失败不得创建数据库文件"
        )

    def test_plain_text_db_empty_succeeds_nonempty_errors(self):
        # 第二组：broken.sqlite 已存在，内容为固定文本 local demo，
        # 不是 SQLite 数据库。空对象导入退出 0、输出 {}\n、stderr
        # 为空，文件字节完全不变；随后非空导入报 STORAGE_ERROR，
        # 文件字节依旧完全不变。
        empty_path = self.write_named_file("empty.json", self.EMPTY_JSON)
        nonempty_path = self.write_named_file("nonempty.json", self.NONEMPTY_JSON)

        db = os.path.join(self.tmpdir, "broken.sqlite")
        with open(db, "wb") as fh:
            fh.write(self.BROKEN_TEXT)
        self.assertTrue(os.path.isfile(db))

        self.assert_import_empty_ok_and_untouched(
            db, "broken.sqlite 为普通文本", empty_path
        )
        with open(db, "rb") as fh:
            after_empty = fh.read()
        self.assertEqual(
            after_empty, self.BROKEN_TEXT,
            "broken: 空对象导入后文件字节必须保持不变，实际: %r" % after_empty,
        )

        self.assert_import_nonempty_error_and_untouched(
            db, "broken.sqlite 为普通文本", nonempty_path
        )
        with open(db, "rb") as fh:
            after_nonempty = fh.read()
        self.assertEqual(
            after_nonempty, self.BROKEN_TEXT,
            "broken: 非空导入失败后文件字节必须保持不变，实际: %r"
            % after_nonempty,
        )


class TestImportWriteFailureRollback(FlagctlCliTestCase):
    """非空 import 在事务内写入失败时的回滚与恢复重试回归。

    固定样例：demo.sqlite 已有标准 flags 表，完整记录仅为
    dev/new_ui=true 与 qa/new_ui=true；settings.json 是 UTF-8 文件，
    内容为 {"new_ui": false}。通过包装进程以与既有入口等价的方式
    （同样的 --db ... import dev settings.json 参数交给
    flagctl.main()）发起导入，仅把 flagctl.connect 换成注入失败的
    版本：代理连接在 executemany 实际落库之后、事务提交之前核对
    dev 行当前值确为 false 且事务仍在进行，把该时序证据写入证据
    文件（不满足时以退出码 99/98 让用例明确失败，不允许用写入之前
    的失败代替），然后抛出 sqlite3.OperationalError 模拟写入错误。

    失败调用退出 2、stdout 为空、stderr 仅为 STORAGE_ERROR 加换行；
    重新打开数据库后 dev 与 qa 均仍为 true，完整 flags 记录与表结构
    与调用前一致，导入文件内容保持原样。随后解除失败条件，用既有
    入口以同一环境同一文件立即重试：退出 0、stderr 为空、stdout 为
    单行 {"new_ui": false} 加换行，dev 的直接设置为 false、qa 仍为
    true，完整记录仅这两条。失败注入只发生在包装进程内，样例准备与
    正常重试按既有行为执行。
    """

    # 包装脚本：以与 ``python flagctl.py --db ... import dev ...`` 等价
    # 的方式调用 flagctl.main()，仅把 flagctl.connect 换成注入失败的
    # 版本。失败注入发生在 executemany 落库之后、提交之前；脚本内对此
    # 自查并留下证据文件，不满足时以 99/98 退出让用例失败。
    WRAPPER_TEMPLATE = '''import os
import sqlite3
import sys

sys.path.insert(0, {here!r})
import flagctl

EVIDENCE = os.environ["FLAGCTL_TEST_EVIDENCE"]

real_connect = flagctl.connect


class WriteFailConnection(object):
    """代理连接：executemany 落库后、事务提交前注入 SQLite 写入错误。"""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def executemany(self, sql, params):
        cursor = self._conn.executemany(sql, params)
        # 时序自查：目标行已在事务内写成 false 且尚未提交；证据写入
        # 证据文件供用例核对，不满足时以 99/98 退出让用例明确失败。
        row = self._conn.execute(
            "SELECT value FROM flags WHERE env = ? AND key = ?",
            ("dev", "new_ui"),
        ).fetchone()
        in_transaction = self._conn.in_transaction
        with open(EVIDENCE, "w", encoding="utf-8") as fh:
            fh.write(
                "value=%r in_transaction=%r\\n"
                % (row[0] if row else None, in_transaction)
            )
        if row is None or row[0] != "false":
            sys.exit(99)
        if not in_transaction:
            sys.exit(98)
        raise sqlite3.OperationalError("simulated import write failure")

    def close(self):
        self._conn.close()


def failing_connect(db_path):
    return WriteFailConnection(real_connect(db_path))


flagctl.connect = failing_connect

sys.exit(flagctl.main())
'''

    def make_demo_db(self):
        """固定样例库 demo.sqlite：标准 flags 表，仅 dev/qa 的 new_ui=true。"""
        db = os.path.join(self.tmpdir, "demo.sqlite")
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
                    [("dev", "new_ui", "true"), ("qa", "new_ui", "true")],
                )
        finally:
            conn.close()
        return db

    def write_wrapper(self):
        """在临时目录写入失败注入包装脚本并返回路径。"""
        wrapper = os.path.join(self.tmpdir, "failing_import.py")
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write(self.WRAPPER_TEMPLATE.format(here=HERE))
        return wrapper

    def run_failing_import(self, wrapper, db, settings, evidence):
        """在包装进程中执行 import dev settings.json，注入事务内写入失败。"""
        env = dict(os.environ)
        env["FLAGCTL_TEST_EVIDENCE"] = evidence
        return subprocess.run(
            [sys.executable, wrapper, "--db", db, "import", "dev", settings],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_write_failure_rolls_back_then_retry_succeeds(self):
        db = self.make_demo_db()
        settings = self.write_import_file('{"new_ui": false}')
        wrapper = self.write_wrapper()
        evidence = os.path.join(self.tmpdir, "evidence.txt")

        rows_before = self.read_flags_rows(db)
        cols_before = self.read_flags_columns(db)
        self.assertEqual(
            rows_before, [("dev", "new_ui", "true"), ("qa", "new_ui", "true")]
        )
        self.assertEqual(cols_before, ["env", "key", "value"])
        with open(settings, "rb") as fh:
            settings_bytes = fh.read()
        self.assertEqual(settings_bytes, b'{"new_ui": false}')

        proc = self.run_failing_import(wrapper, db, settings, evidence)

        # 退出 2、stdout 为空、stderr 仅为 STORAGE_ERROR 加换行；若注入
        # 发生在写入之前或事务已提交，包装进程会以 99/98 退出，下面的
        # 断言随之失败。
        self.assertCommandError(proc, "STORAGE_ERROR")

        # 可核对的时序证据：失败发生时目标行已在事务内写成 false 且
        # 尚未提交，回滚验证确实涉及已发生的修改。
        self.assertTrue(os.path.isfile(evidence), "注入点必须留下时序证据文件")
        with open(evidence, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "value='false' in_transaction=True\n")

        # 回滚后重新打开数据库：dev 与 qa 均仍为 true，完整记录与表结构
        # 与调用前一致，导入文件内容保持原样。
        self.assertEqual(self.read_table_names(db), ["flags"])
        self.assertEqual(self.read_flags_columns(db), cols_before)
        self.assertEqual(self.read_flags_rows(db), rows_before)
        with open(settings, "rb") as fh:
            self.assertEqual(fh.read(), settings_bytes, "导入文件内容必须保持原样")

        # 解除失败条件，用既有入口以同一环境同一文件立即重试。
        retry = self.import_flags(db, "dev", settings)
        self.assertCommandOk(retry, '{"new_ui": false}')

        # 重试后 dev 的直接设置为 false、qa 仍为 true，完整记录仅这两条，
        # 证明失败没有留下妨碍后续导入的状态。
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )
        self.assertEqual(self.read_flags_columns(db), cols_before)
        with open(settings, "rb") as fh:
            self.assertEqual(fh.read(), settings_bytes, "重试后导入文件内容必须保持原样")


class TestImportDryRunPreview(FlagctlCliTestCase):
    """import --dry-run 只读预览差异的命令行回归测试。

    主样例固定为 demo.sqlite 中 dev/new_ui=true、qa/new_ui=false，
    candidate.json 是内容为 {"new_ui":false} 的 UTF-8 文件：预览
    stdout 为单行 {"new_ui":{"before":true,"after":false}} 加换行，
    退出 0、stderr 为空，随后 get dev new_ui 仍输出 true。另覆盖
    原值已为 false（预览 {}）、目标记录缺失与库文件缺失/缺 flags 表
    （before 为 null，未设置不被当成 false，不补建文件或表）、空对象
    在父目录缺失时仍输出 {} 且不访问存储（同路径非空文件报
    STORAGE_ERROR）、目标值损坏报 STORAGE_ERROR 且不输出部分结果、
    其他环境与未知键的异常值不影响合法目标。

    每次预览前后都核对输入文件字节、库文件字节、完整记录与表结构；
    失败一律退出 2、stdout 为空、stderr 仅为错误码加换行。验收不仅
    看退出码，还同时检查输出内容与调用后的文件状态。
    """

    def make_demo_db(self):
        """固定主样例库 demo.sqlite：dev/new_ui=true、qa/new_ui=false。"""
        db = os.path.join(self.tmpdir, "demo.sqlite")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "false")],
        )
        return db

    def write_candidate(self, content=b'{"new_ui":false}', name="candidate.json"):
        """按固定文件名写入 UTF-8 导入文件（默认 candidate.json）。"""
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as fh:
            fh.write(content)
        return path

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

    def create_db_without_flags_table(self, db):
        """创建只含无关表 notes（含一行数据）的有效 SQLite 库。"""
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO notes (text) VALUES ('sample note')")
        finally:
            conn.close()

    def read_notes_rows(self, db):
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT id, text FROM notes ORDER BY id").fetchall()
        finally:
            conn.close()

    def snapshot_storage(self, db):
        """采集库文件存在性、字节、表清单及 flags 表结构与完整记录。"""
        snapshot = {"exists": os.path.exists(db)}
        if snapshot["exists"]:
            snapshot["bytes"] = self.read_file_bytes(db)
            conn = sqlite3.connect(db)
            try:
                tables = [
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "ORDER BY name"
                    ).fetchall()
                ]
                snapshot["tables"] = tables
                if "flags" in tables:
                    snapshot["columns"] = [
                        row[1]
                        for row in conn.execute("PRAGMA table_info(flags)").fetchall()
                    ]
                    snapshot["rows"] = conn.execute(
                        "SELECT env, key, value FROM flags ORDER BY env, key"
                    ).fetchall()
                else:
                    snapshot["columns"] = None
                    snapshot["rows"] = None
            finally:
                conn.close()
        return snapshot

    def assert_storage_snapshot_unchanged(self, db, before):
        """调用后的存储现场（文件字节、表结构、完整记录）必须与快照一致。"""
        self.assertEqual(self.snapshot_storage(db), before)

    def test_fixed_sample_previews_change_without_writing(self):
        # 主样例：demo.sqlite 中 dev/new_ui=true、qa/new_ui=false，
        # candidate.json 内容为 {"new_ui":false}。预览 stdout 为单行
        # {"new_ui":{"before":true,"after":false}} 加换行，退出 0、
        # stderr 为空；预览不写入，get dev new_ui 仍输出 true，qa 保留。
        db = self.make_demo_db()
        candidate = self.write_candidate()
        self.assertEqual(self.read_file_bytes(candidate), b'{"new_ui":false}')

        before = self.snapshot_storage(db)
        proc = self.preview_flags(db, "dev", candidate)

        self.assertCommandOk(
            proc, '{"new_ui":{"before":true,"after":false}}'
        )
        # 输入文件字节与库现场（文件字节、表结构、完整记录）保持一致。
        self.assertEqual(self.read_file_bytes(candidate), b'{"new_ui":false}')
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "false")],
        )
        self.assertEqual(self.read_table_names(db), ["flags"])
        self.assertEqual(self.read_flags_columns(db), ["env", "key", "value"])

        # 随后正式读取：dev 仍是 true（false 是预览值而非已落库的值），
        # qa 的记录也保留为 false。
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_existing_false_previews_empty_object(self):
        # 原值已经是 false 时没有变化：预览为 {}，退出 0、stderr 为空，
        # 库文件字节、完整记录与表结构保持不变，dev 仍读出 false。
        db = self.db_path("already_false")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        candidate = self.write_candidate(name="candidate_false.json")

        before = self.snapshot_storage(db)
        proc = self.preview_flags(db, "dev", candidate)

        self.assertCommandOk(proc, "{}")
        self.assertEqual(self.read_file_bytes(candidate), b'{"new_ui":false}')
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "false")],
        )
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "false")

    def test_missing_target_row_previews_null_before(self):
        # 目标环境没有目标记录：before 为 null、after 为 false；未设置
        # 不能被当成 false——预览后 dev 仍无记录（get 报 VALUE_NOT_SET），
        # 也不新增行；qa 的既有记录保留。
        db = self.db_path("missing_row")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "false")])
        candidate = self.write_candidate(name="candidate_null_row.json")

        before = self.snapshot_storage(db)
        proc = self.preview_flags(db, "dev", candidate)

        self.assertCommandOk(
            proc, '{"new_ui":{"before":null,"after":false}}'
        )
        self.assertEqual(self.read_file_bytes(candidate), b'{"new_ui":false}')
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(self.read_flags_rows(db), [("qa", "new_ui", "false")])
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "VALUE_NOT_SET")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def test_missing_db_file_previews_null_without_creating_file(self):
        # 父目录存在而库文件缺失：非空文件预览以 null 表示原值，
        # 退出 0，不补建库文件，临时目录内文件清单不变。
        db = self.db_path("preview_missing_db")
        self.assertFalse(os.path.exists(db))
        candidate = self.write_candidate(name="candidate_missing_db.json")
        listing_before = sorted(os.listdir(self.tmpdir))

        proc = self.preview_flags(db, "dev", candidate)

        self.assertCommandOk(
            proc, '{"new_ui":{"before":null,"after":false}}'
        )
        self.assertFalse(os.path.exists(db), "预览不得补建数据库文件: %s" % db)
        self.assertEqual(
            sorted(os.listdir(self.tmpdir)),
            listing_before,
            "预览不得在临时目录内新增任何文件",
        )
        self.assertEqual(self.read_file_bytes(candidate), b'{"new_ui":false}')

    def test_valid_db_without_flags_table_previews_null_without_creating_table(self):
        # 有效库没有 flags 表：非空文件预览以 null 表示原值，不补建
        # flags 表，原有表与其数据、库文件字节均保持不变。
        db = self.db_path("preview_no_flags_table")
        self.create_db_without_flags_table(db)
        self.assertEqual(self.read_table_names(db), ["notes"])
        candidate = self.write_candidate(name="candidate_no_table.json")

        before = self.snapshot_storage(db)
        proc = self.preview_flags(db, "dev", candidate)

        self.assertCommandOk(
            proc, '{"new_ui":{"before":null,"after":false}}'
        )
        self.assertEqual(self.read_file_bytes(candidate), b'{"new_ui":false}')
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(self.read_table_names(db), ["notes"])
        self.assertEqual(self.read_notes_rows(db), [(1, "sample note")])

    def test_empty_object_missing_parent_succeeds_nonempty_errors(self):
        # 合法空对象 {} 在库路径父目录不存在时仍成功输出 {}，不创建
        # 目录或访问存储；相同库路径改用非空文件则报 STORAGE_ERROR，
        # 目录与库文件仍不存在。两种调用的输入文件字节都保持不变。
        missing_dir = os.path.join(self.tmpdir, "no_such_parent")
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        empty_file = self.write_candidate(b"{}", name="empty.json")
        nonempty_file = self.write_candidate(
            b'{"new_ui":false}', name="nonempty.json"
        )

        empty_proc = self.preview_flags(db, "dev", empty_file)
        self.assertCommandOk(empty_proc, "{}")
        self.assertFalse(
            os.path.exists(missing_dir), "空对象预览不得创建缺失的父目录"
        )
        self.assertFalse(os.path.exists(db), "空对象预览不得创建数据库文件")
        self.assertEqual(self.read_file_bytes(empty_file), b"{}")

        nonempty_proc = self.preview_flags(db, "dev", nonempty_file)
        self.assertCommandError(nonempty_proc, "STORAGE_ERROR")
        self.assertFalse(
            os.path.exists(missing_dir), "非空预览失败不得创建缺失的父目录"
        )
        self.assertFalse(os.path.exists(db), "非空预览失败不得创建数据库文件")
        self.assertEqual(
            self.read_file_bytes(nonempty_file), b'{"new_ui":false}'
        )

    def test_corrupt_target_value_reports_storage_error_without_partial_output(self):
        # 目标 new_ui 的存储文本为 yes：报 STORAGE_ERROR，退出 2、
        # stdout 为空、stderr 仅为 STORAGE_ERROR 加换行；保留原值 yes，
        # 不输出部分结果，库文件字节、完整记录与表结构不变，qa 保留。
        db = self.db_path("preview_bad_target")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")],
        )
        candidate = self.write_candidate(name="candidate_bad_target.json")

        before = self.snapshot_storage(db)
        proc = self.preview_flags(db, "dev", candidate)

        self.assertCommandError(proc, "STORAGE_ERROR")
        self.assertEqual(
            self.read_file_bytes(candidate), b'{"new_ui":false}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "yes"), ("qa", "new_ui", "false")],
        )
        self.assertEqual(self.read_flags_columns(db), ["env", "key", "value"])
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")
        self.assertCommandError(self.get_flag(db, "dev", "new_ui"), "STORAGE_ERROR")

    def test_invalid_value_in_other_env_does_not_affect_preview(self):
        # 其他环境的异常值不在目标范围内：qa/new_ui=yes 不影响 dev 的
        # 合法预览；预览后异常原值原样保留，库文件字节与完整记录不变。
        db = self.db_path("preview_other_env_bad")
        self.seed_flags_table(
            db,
            [("dev", "new_ui", "true"), ("qa", "new_ui", "yes")],
        )
        candidate = self.write_candidate(name="candidate_other_env.json")

        before = self.snapshot_storage(db)
        proc = self.preview_flags(db, "dev", candidate)

        self.assertCommandOk(
            proc, '{"new_ui":{"before":true,"after":false}}'
        )
        self.assertEqual(
            self.read_file_bytes(candidate), b'{"new_ui":false}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "yes")],
        )

    def test_anomalous_unknown_key_values_do_not_affect_preview(self):
        # 未知键（含其异常值，无论是否在目标环境内）一律忽略：
        # 合法目标 dev/new_ui=true 的预览正常输出差异，预览后未知键
        # 异常行原样保留，库文件字节与完整记录不变。
        db = self.db_path("preview_unknown_key_bad")
        self.seed_flags_table(
            db,
            [
                ("dev", "new_ui", "true"),
                ("dev", "other_key", "yes"),
                ("qa", "other_key", "maybe"),
            ],
        )
        candidate = self.write_candidate(name="candidate_unknown_key.json")

        before = self.snapshot_storage(db)
        proc = self.preview_flags(db, "dev", candidate)

        self.assertCommandOk(
            proc, '{"new_ui":{"before":true,"after":false}}'
        )
        self.assertEqual(
            self.read_file_bytes(candidate), b'{"new_ui":false}'
        )
        self.assert_storage_snapshot_unchanged(db, before)
        self.assertEqual(
            self.read_flags_rows(db),
            [
                ("dev", "new_ui", "true"),
                ("dev", "other_key", "yes"),
                ("qa", "other_key", "maybe"),
            ],
        )


class TestImportDryRunRejectionOrder(FlagctlCliTestCase):
    """import --dry-run 的输入拒绝顺序与文件保护回归。

    库路径的父目录始终不存在：所有输入错误都必须先于任何存储访问
    报告，不得被 STORAGE_ERROR 取代。校验顺序为环境名 -> 读文件 ->
    JSON 解析（含非标准常量）-> 全部键名 -> 全部布尔值。每个失败
    调用退出 2、stdout 为空、stderr 仅为错误码加换行（不附带堆栈
    或预览对象）；调用后缺失的父目录与库文件仍不存在，已有输入
    文件字节保持原样。
    """

    def missing_parent_db(self, label):
        """返回父目录与库文件均不存在的库路径及其父目录。"""
        missing_dir = os.path.join(self.tmpdir, label)
        db = os.path.join(missing_dir, "flags.sqlite")
        self.assertFalse(os.path.exists(missing_dir))
        self.assertFalse(os.path.exists(db))
        return missing_dir, db

    def assert_parent_still_missing(self, missing_dir, db):
        """调用后缺失的父目录与库文件必须仍然不存在。"""
        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(db), "不得创建数据库文件")

    def test_blank_env_with_missing_file_reports_only_empty_env(self):
        # 纯空白环境名搭配不存在的输入文件：只报 EMPTY_ENV，
        # 既不是 IMPORT_READ_ERROR 也不是 STORAGE_ERROR。
        missing_dir, db = self.missing_parent_db("dryrun_blank_env_dir")
        missing_file = os.path.join(self.tmpdir, "no_such_candidate.json")
        self.assertFalse(os.path.exists(missing_file))

        proc = self.preview_flags(db, "   ", missing_file)

        self.assertCommandError(proc, "EMPTY_ENV")
        self.assert_parent_still_missing(missing_dir, db)
        self.assertFalse(os.path.exists(missing_file), "不得创建输入文件")

    def test_valid_env_with_missing_file_reports_only_import_read_error(self):
        # 合法环境名搭配不存在的输入文件：只报 IMPORT_READ_ERROR。
        missing_dir, db = self.missing_parent_db("dryrun_missing_file_dir")
        missing_file = os.path.join(self.tmpdir, "no_such_candidate.json")
        self.assertFalse(os.path.exists(missing_file))

        proc = self.preview_flags(db, "dev", missing_file)

        self.assertCommandError(proc, "IMPORT_READ_ERROR")
        self.assert_parent_still_missing(missing_dir, db)
        self.assertFalse(os.path.exists(missing_file), "不得创建输入文件")

    def test_bare_constant_reports_invalid_json_before_key_check(self):
        # {"other_key":NaN}：非标准 JSON 常量的拒绝先于键名校验，
        # 只报 INVALID_JSON 而非 UNKNOWN_KEY；输入文件字节保持原样。
        missing_dir, db = self.missing_parent_db("dryrun_constant_dir")
        content = b'{"other_key":NaN}'
        path = self.write_import_file(content, name="constant.json", raw=True)

        proc = self.preview_flags(db, "dev", path)

        self.assertCommandError(proc, "INVALID_JSON")
        self.assert_parent_still_missing(missing_dir, db)
        self.assertEqual(self.read_file_bytes(path), content, "输入文件字节必须保持原样")

    def test_unknown_key_checked_before_bool_values(self):
        # {"new_ui":"false","other_key":true}：全部键名的检查先于
        # 布尔值检查，只报 UNKNOWN_KEY 而非 INVALID_BOOL。
        missing_dir, db = self.missing_parent_db("dryrun_unknown_key_dir")
        content = b'{"new_ui":"false","other_key":true}'
        path = self.write_import_file(content, name="unknown_key.json", raw=True)

        proc = self.preview_flags(db, "dev", path)

        self.assertCommandError(proc, "UNKNOWN_KEY")
        self.assert_parent_still_missing(missing_dir, db)
        self.assertEqual(self.read_file_bytes(path), content, "输入文件字节必须保持原样")

    def test_string_bool_reports_only_invalid_bool(self):
        # 仅含 {"new_ui":"false"}：字符串不能被当作布尔值，
        # 只报 INVALID_BOOL。
        missing_dir, db = self.missing_parent_db("dryrun_string_bool_dir")
        content = b'{"new_ui":"false"}'
        path = self.write_import_file(content, name="string_bool.json", raw=True)

        proc = self.preview_flags(db, "dev", path)

        self.assertCommandError(proc, "INVALID_BOOL")
        self.assert_parent_still_missing(missing_dir, db)
        self.assertEqual(self.read_file_bytes(path), content, "输入文件字节必须保持原样")


class TestImportDryRunSequentialContrast(FlagctlCliTestCase):
    """同一有效 demo.sqlite 上连续两次 --dry-run 预览的对照回归。

    预置 dev/new_ui=true、qa/new_ui=false。第一次用内容为
    {"new_ui":"false"} 的 candidate.json 预览 dev：字符串不是
    布尔值，报 INVALID_BOOL；随后把输入改为 {"new_ui":false} 再
    预览：退出 0、stderr 为空，stdout 为单行
    {"new_ui":{"before":true,"after":false}} 加换行。两次调用均
    不改变数据库的完整记录、表结构与文件字节，也不改动各自的
    输入文件；预览后 dev 仍为 true、qa 仍为 false。
    """

    def make_demo_db(self):
        """固定样例库 demo.sqlite：dev/new_ui=true、qa/new_ui=false。"""
        db = os.path.join(self.tmpdir, "demo.sqlite")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "false")],
        )
        return db

    def write_candidate(self, content):
        """以固定文件名 candidate.json 写入 UTF-8 字节并返回路径。"""
        path = os.path.join(self.tmpdir, "candidate.json")
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    def snapshot_demo_db(self, db):
        """采集库文件字节、表清单、flags 表结构与完整记录。"""
        return {
            "bytes": self.read_file_bytes(db),
            "tables": self.read_table_names(db),
            "columns": self.read_flags_columns(db),
            "rows": self.read_flags_rows(db),
        }

    def test_invalid_bool_then_valid_preview_keep_db_and_files_untouched(self):
        db = self.make_demo_db()
        candidate = self.write_candidate(b'{"new_ui":"false"}')

        # 第一次预览：{"new_ui":"false"} 的字符串值不是布尔值，
        # 报 INVALID_BOOL，数据库与输入文件保持原样。
        db_before = self.snapshot_demo_db(db)
        first = self.preview_flags(db, "dev", candidate)

        self.assertCommandError(first, "INVALID_BOOL")
        self.assertEqual(self.snapshot_demo_db(db), db_before, "预览失败不得改动数据库")
        self.assertEqual(
            self.read_file_bytes(candidate),
            b'{"new_ui":"false"}',
            "预览不得改动输入文件",
        )

        # 把输入改为合法布尔值后再次预览：输出 before/after 差异，
        # 数据库与输入文件依旧保持原样。
        candidate = self.write_candidate(b'{"new_ui":false}')
        db_before = self.snapshot_demo_db(db)
        second = self.preview_flags(db, "dev", candidate)

        self.assertCommandOk(second, '{"new_ui":{"before":true,"after":false}}')
        self.assertEqual(self.snapshot_demo_db(db), db_before, "预览不得改动数据库")
        self.assertEqual(
            self.read_file_bytes(candidate),
            b'{"new_ui":false}',
            "预览不得改动输入文件",
        )

        # 两次预览都不落库：dev 仍为 true、qa 仍为 false。
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "true"), ("qa", "new_ui", "false")],
        )
        self.assertEqual(self.read_table_names(db), ["flags"])
        self.assertEqual(self.read_flags_columns(db), ["env", "key", "value"])
        self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")


class TestImportDuplicateKeyRejection(FlagctlCliTestCase):
    """import 与 import --dry-run 对重复 JSON 键的一致拒绝回归。

    固定样例：demo.sqlite 只保存 dev/new_ui=true 与 qa/new_ui=false，
    candidate.json 为 UTF-8 文本。每份输入都分别以正式导入
    （import dev candidate.json）与只读预览（同一命令附加
    --dry-run）调用，两种导入方式对同一文件必须给出一致的拒绝
    结果，且输入校验先于任何数据库访问：

    * {"new_ui":true,"\\u006eew_ui":false}：\\u006e 解码为 n，
      两个键解码后都是 new_ui，均报 INVALID_JSON；
    * {"new_ui":{"x":1,"x":2}} 与 {"other_key":{"x":1,"x":2}}：
      嵌套对象内的重复键先于布尔值与未知键校验，均报 INVALID_JSON；
    * {"new_ui":[{"x":1},{"x":2}]}（对照）：不同对象各自出现一次
      x 不构成重复，数组本身不是合法开关值，均报 INVALID_BOOL。

    每次失败退出 2、stdout 为空、stderr 仅为对应错误码加换行，没有
    堆栈或部分预览结果。各用例独立准备输入，调用后 candidate.json
    原始字节、库文件字节与全部配置记录保持原样，dev 与 qa 的设置
    不被覆盖。另在父目录已存在、库文件尚不存在的路径上验证同样的
    拒绝结果，调用结束后库文件仍不存在——解析失败不会顺手创建
    配置库。
    """

    # 固定样例库的完整记录（按主键升序）。
    DEMO_ROWS = [("dev", "new_ui", "true"), ("qa", "new_ui", "false")]

    def make_case_dir(self, label, mode):
        """为（用例, 导入方式）组合创建独立目录，保证各用例独立准备输入。"""
        case_dir = os.path.join(self.tmpdir, "%s_%s" % (label, mode))
        os.mkdir(case_dir)
        return case_dir

    def make_demo_db(self, case_dir):
        """在指定目录创建固定样例库 demo.sqlite：dev=true、qa=false。"""
        db = os.path.join(case_dir, "demo.sqlite")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "true"), "true")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "false"), "false")
        self.assertEqual(self.read_flags_rows(db), self.DEMO_ROWS)
        return db

    def write_candidate(self, case_dir, content):
        """在指定目录以固定文件名 candidate.json 写入原始字节。"""
        path = os.path.join(case_dir, "candidate.json")
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    def run_import_mode(self, mode, db, candidate):
        """按导入方式执行：正式导入或附加 --dry-run 的只读预览。"""
        if mode == "import":
            return self.import_flags(db, "dev", candidate)
        return self.preview_flags(db, "dev", candidate)

    def check_rejected_consistently(self, label, content, code):
        """同一输入在正式导入与只读预览下得到同一拒绝结果且现场不变。"""
        for mode in ("import", "dry-run"):
            with self.subTest(content=content, mode=mode):
                case_dir = self.make_case_dir(label, mode)
                db = self.make_demo_db(case_dir)
                candidate = self.write_candidate(case_dir, content)
                db_bytes = self.read_file_bytes(db)

                proc = self.run_import_mode(mode, db, candidate)

                # 退出 2、stdout 为空、stderr 仅为错误码加换行。
                self.assertCommandError(proc, code)
                # 输入文件与库文件字节、表结构与全部记录保持原样。
                self.assertEqual(
                    self.read_file_bytes(candidate),
                    content,
                    "调用不得改动输入文件 candidate.json",
                )
                self.assertEqual(
                    self.read_file_bytes(db),
                    db_bytes,
                    "校验失败不得改动库文件字节",
                )
                self.assertEqual(self.read_table_names(db), ["flags"])
                self.assertEqual(
                    self.read_flags_columns(db), ["env", "key", "value"]
                )
                self.assertEqual(self.read_flags_rows(db), self.DEMO_ROWS)
                # dev 与 qa 的设置不被覆盖。
                self.assertCommandOk(self.get_flag(db, "dev", "new_ui"), "true")
                self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "false")

    def check_rejected_without_creating_db(self, label, content, code):
        """父目录存在、库文件不存在：同一拒绝结果，且不创建库文件。"""
        for mode in ("import", "dry-run"):
            with self.subTest(content=content, mode=mode):
                case_dir = self.make_case_dir(label + "_fresh", mode)
                db = os.path.join(case_dir, "demo.sqlite")
                self.assertFalse(os.path.exists(db))
                candidate = self.write_candidate(case_dir, content)

                proc = self.run_import_mode(mode, db, candidate)

                self.assertCommandError(proc, code)
                self.assertFalse(
                    os.path.exists(db),
                    "解析失败不得顺手创建数据库文件: %s" % db,
                )
                self.assertEqual(
                    self.read_file_bytes(candidate),
                    content,
                    "调用不得改动输入文件 candidate.json",
                )

    def test_escaped_duplicate_key_rejected(self):
        # 文件内容 {"new_ui":true,"\u006eew_ui":false} 中的 JSON 转义
        # \u006e 解码为 n，两个键解码后都是 new_ui，构成顶层重复键。
        content = b'{"new_ui":true,"\\u006eew_ui":false}'
        self.check_rejected_consistently("escaped_dup", content, "INVALID_JSON")
        self.check_rejected_without_creating_db(
            "escaped_dup", content, "INVALID_JSON"
        )

    def test_nested_duplicate_under_known_key_rejected(self):
        # {"new_ui":{"x":1,"x":2}}：嵌套对象内的重复键在解析阶段即被
        # 拒绝，先于布尔值校验（对象本身也不是合法开关值）。
        content = b'{"new_ui":{"x":1,"x":2}}'
        self.check_rejected_consistently("nested_known", content, "INVALID_JSON")
        self.check_rejected_without_creating_db(
            "nested_known", content, "INVALID_JSON"
        )

    def test_nested_duplicate_under_unknown_key_rejected(self):
        # {"other_key":{"x":1,"x":2}}：嵌套重复键的拒绝先于未知键
        # 校验，报 INVALID_JSON 而非 UNKNOWN_KEY。
        content = b'{"other_key":{"x":1,"x":2}}'
        self.check_rejected_consistently("nested_unknown", content, "INVALID_JSON")
        self.check_rejected_without_creating_db(
            "nested_unknown", content, "INVALID_JSON"
        )

    def test_distinct_objects_in_array_are_not_duplicates(self):
        # 对照样例 {"new_ui":[{"x":1},{"x":2}]}：两个不同对象各自
        # 出现一次 x 不构成重复键，解析通过；数组不是合法开关值，
        # 报 INVALID_BOOL。
        content = b'{"new_ui":[{"x":1},{"x":2}]}'
        self.check_rejected_consistently("array_control", content, "INVALID_BOOL")
        self.check_rejected_without_creating_db(
            "array_control", content, "INVALID_BOOL"
        )


class TestExport(FlagctlCliTestCase):
    """export 把一个环境已保存的直接设置导出为单环境 JSON 文件。

    输出协议：成功时退出 0、stderr 为空，stdout 与输出文件同为单行
    JSON 对象加换行（无 BOM 的 UTF-8）；失败时退出 2、stdout 为空、
    stderr 仅为错误码加换行，且不创建或改动输出文件。处理顺序为
    环境名校验 -> 完整读取配置 -> 输出文件检查与写入；全程不改动源库。
    """

    def export_path(self, name="out.json"):
        """返回临时目录中一个（尚不存在的）输出文件路径。"""
        return os.path.join(self.tmpdir, name)

    def assertExportOk(self, proc, path, expected):
        """退出 0、stderr 为空；stdout 与文件都是同一单行 JSON 加换行。"""
        self.assertEqual(
            proc.returncode, 0, "期望退出码 0，实际 stderr: %r" % proc.stderr
        )
        self.assertEqual(proc.stderr, "")
        self.assertTrue(proc.stdout.endswith("\n"), "输出必须以换行结束")
        body = proc.stdout[:-1]
        self.assertNotIn("\n", body, "JSON 输出必须只有一行")
        self.assertEqual(json.loads(body), expected)
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertFalse(
            raw.startswith(b"\xef\xbb\xbf"), "输出文件不得带 UTF-8 BOM"
        )
        self.assertEqual(raw.decode("utf-8"), proc.stdout, "文件内容必须与 stdout 一致")
        self.assertEqual(json.loads(raw.decode("utf-8")), expected)

    def make_fixed_sample_db(self, label="demo"):
        """固定样例库：dev/new_ui=false，qa/new_ui=true。"""
        db = self.db_path(label)
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        return db

    def test_fixed_sample_export_then_import_into_other_db(self):
        # 固定样例：导出 dev 得到 {"new_ui": false}，再导入另一库的
        # review 环境，源库与目标库其余记录保持原样。
        db = self.make_fixed_sample_db()
        path = self.export_path("dev.json")
        self.assertFalse(os.path.exists(path))

        self.assertExportOk(self.export_flags(db, "dev", path), path, {"new_ui": False})

        copy_db = self.db_path("copy")
        self.assertCommandOk(
            self.import_flags(copy_db, "review", path), '{"new_ui": false}'
        )
        self.assertCommandOk(self.get_flag(copy_db, "review", "new_ui"), "false")
        # 源库保持原值。
        self.assertEqual(
            self.read_flags_rows(db),
            [("dev", "new_ui", "false"), ("qa", "new_ui", "true")],
        )

    def test_repeat_export_reports_export_exists_and_keeps_content(self):
        db = self.make_fixed_sample_db()
        path = self.export_path()
        self.assertExportOk(self.export_flags(db, "dev", path), path, {"new_ui": False})
        with open(path, "rb") as fh:
            original = fh.read()

        self.assertCommandError(self.export_flags(db, "dev", path), "EXPORT_EXISTS")

        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), original, "EXPORT_EXISTS 不得改动原文件")

    def test_empty_env_rejected_before_any_output(self):
        db = self.make_fixed_sample_db()
        for env in ("", "   ", "\t\n "):
            path = self.export_path()
            self.assertCommandError(self.export_flags(db, env, path), "EMPTY_ENV")
            self.assertFalse(os.path.exists(path), "EMPTY_ENV 不得创建输出文件")

    def test_env_surrounding_whitespace_matches_stripped(self):
        db = self.make_fixed_sample_db()
        path = self.export_path()
        self.assertExportOk(
            self.export_flags(db, "  dev  ", path), path, {"new_ui": False}
        )

    def test_env_name_is_case_sensitive(self):
        db = self.make_fixed_sample_db()
        # Dev 与 dev 是不同环境：Dev 没有任何直接设置，导出 {}。
        path = self.export_path()
        self.assertExportOk(self.export_flags(db, "Dev", path), path, {})

    def test_output_path_same_as_db_reports_export_write_error(self):
        db = self.make_fixed_sample_db()
        with open(db, "rb") as fh:
            before = fh.read()
        # 同一文件的另一种写法（./ 前缀）也算同一路径。
        same = os.path.join(os.path.dirname(db), ".", os.path.basename(db))

        self.assertCommandError(
            self.export_flags(db, "dev", same), "EXPORT_WRITE_ERROR"
        )

        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), before, "源库字节必须保持不变")

    def test_missing_db_file_exports_empty_object_without_creating_db(self):
        db = self.db_path("missing")
        path = self.export_path()

        self.assertExportOk(self.export_flags(db, "dev", path), path, {})

        self.assertFalse(os.path.exists(db), "导出不得创建数据库文件")

    def test_valid_db_without_flags_table_exports_empty_object(self):
        db = self.db_path("no_table")
        conn = sqlite3.connect(db)
        conn.close()
        path = self.export_path()

        self.assertExportOk(self.export_flags(db, "dev", path), path, {})

        self.assertEqual(self.read_table_names(db), [], "导出不得补建 flags 表")

    def test_env_without_settings_exports_empty_object(self):
        db = self.make_fixed_sample_db()
        self.assertExportOk(
            self.export_flags(db, "staging", self.export_path()),
            self.export_path(),
            {},
        )

    def test_missing_db_parent_directory_reports_storage_error(self):
        missing_dir = os.path.join(self.tmpdir, "no_such_dir")
        db = os.path.join(missing_dir, "flags.sqlite")
        path = self.export_path()

        self.assertCommandError(self.export_flags(db, "dev", path), "STORAGE_ERROR")

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的父目录")
        self.assertFalse(os.path.exists(path), "STORAGE_ERROR 不得创建输出文件")

    def test_plain_text_db_reports_storage_error(self):
        db = self.db_path("plain_text")
        content = b"this is not a sqlite database\n"
        with open(db, "wb") as fh:
            fh.write(content)
        path = self.export_path()

        self.assertCommandError(self.export_flags(db, "dev", path), "STORAGE_ERROR")

        self.assertFalse(os.path.exists(path))
        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), content, "源文件字节必须保持不变")

    def test_flags_table_without_value_column_reports_storage_error(self):
        db = self.db_path("no_value_column")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags ("
                    "env TEXT NOT NULL, key TEXT NOT NULL, "
                    "PRIMARY KEY (env, key))"
                )
        finally:
            conn.close()
        path = self.export_path()

        self.assertCommandError(self.export_flags(db, "dev", path), "STORAGE_ERROR")

        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.read_flags_columns(db), ["env", "key"])

    def test_target_env_invalid_value_reports_storage_error(self):
        # 目标环境的合法键存有非法值：报 STORAGE_ERROR，不创建输出文件。
        db = self.db_path("bad_value")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags (env TEXT NOT NULL, key TEXT NOT NULL, "
                    "value TEXT NOT NULL, PRIMARY KEY (env, key))"
                )
                conn.execute(
                    "INSERT INTO flags VALUES (?, ?, ?)", ("dev", "new_ui", "yes")
                )
        finally:
            conn.close()
        path = self.export_path()

        self.assertCommandError(self.export_flags(db, "dev", path), "STORAGE_ERROR")

        self.assertFalse(os.path.exists(path))
        self.assertEqual(
            self.read_flags_rows(db), [("dev", "new_ui", "yes")], "异常记录保持原样"
        )

    def test_other_env_and_unknown_key_anomalies_are_ignored(self):
        # 其他环境的异常值与未知键（含其异常值）不影响目标环境的导出。
        db = self.db_path("mixed")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags (env TEXT NOT NULL, key TEXT NOT NULL, "
                    "value TEXT NOT NULL, PRIMARY KEY (env, key))"
                )
                conn.executemany(
                    "INSERT INTO flags VALUES (?, ?, ?)",
                    [
                        ("dev", "new_ui", "false"),
                        ("qa", "new_ui", "junk"),
                        ("dev", "mystery_key", "junk"),
                    ],
                )
        finally:
            conn.close()

        path = self.export_path()
        self.assertExportOk(
            self.export_flags(db, "dev", path), path, {"new_ui": False}
        )

    def test_missing_output_parent_directory_reports_export_write_error(self):
        db = self.make_fixed_sample_db()
        missing_dir = os.path.join(self.tmpdir, "no_such_output_dir")
        path = os.path.join(missing_dir, "out.json")

        self.assertCommandError(
            self.export_flags(db, "dev", path), "EXPORT_WRITE_ERROR"
        )

        self.assertFalse(os.path.exists(missing_dir), "不得创建缺失的输出目录")
        self.assertFalse(os.path.exists(path))

    def test_export_does_not_modify_source_db(self):
        # 成功导出全程只读源库：导出前后文件字节完全一致。
        db = self.make_fixed_sample_db()
        with open(db, "rb") as fh:
            before = fh.read()

        path = self.export_path()
        self.assertExportOk(
            self.export_flags(db, "dev", path), path, {"new_ui": False}
        )

        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), before)


class TestExportErrorPrecedence(FlagctlCliTestCase):
    """export 处理顺序（环境名 -> 完整读取配置 -> 输出文件）的组合回归。

    固定样例：临时目录（数据库父目录）已经存在，demo.sqlite 是有效
    SQLite 库，flags 表仅含 dev/new_ui=yes（非法存储值）与
    qa/new_ui=true 两条记录，out.json 预先保存 UTF-8 文本 KEEP。
    多项错误条件同时存在时，结果由处理顺序唯一决定：

    * 环境名为空字符串或仅含空格、制表符 -> 只报 EMPTY_ENV，非法
      存储值与已存在的 out.json 都不参与判定；
    * 环境为 dev -> 只报 STORAGE_ERROR，已存在的 out.json 不能使
      结果变为 EXPORT_EXISTS；
    * 环境为 dev 且输出路径就是 demo.sqlite 本身（含带 ./ 的等价
      写法）-> 仍只报 STORAGE_ERROR，不能先报 EXPORT_WRITE_ERROR。

    每次失败退出 2、stdout 为空、stderr 严格为错误码加换行，不含
    提示文本或部分 JSON；调用后源库文件字节与全部记录（dev 的 yes
    不被修正、qa 的 true 原样保留）、out.json 的 KEEP 内容与临时
    目录文件清单均保持不变。每个用例独立准备样例，互不以前序用例
    的结果为前提；检查阶段只读取既有文件，不补建数据库或表。
    """

    FIXED_ROWS = [("dev", "new_ui", "yes"), ("qa", "new_ui", "true")]

    def make_fixed_sample(self):
        """独立准备固定样例，返回 (demo.sqlite 路径, out.json 路径)。"""
        db = os.path.join(self.tmpdir, "demo.sqlite")
        conn = sqlite3.connect(db)
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE flags (env TEXT NOT NULL, key TEXT NOT NULL, "
                    "value TEXT NOT NULL, PRIMARY KEY (env, key))"
                )
                conn.executemany(
                    "INSERT INTO flags VALUES (?, ?, ?)", self.FIXED_ROWS
                )
        finally:
            conn.close()
        out_path = os.path.join(self.tmpdir, "out.json")
        with open(out_path, "wb") as fh:
            fh.write("KEEP".encode("utf-8"))
        return db, out_path

    def snapshot_sample(self, db, out_path):
        """采集调用前的现场：库字节、全部记录、out.json 字节、目录清单。"""
        return (
            self.read_file_bytes(db),
            self.read_flags_rows(db),
            self.read_file_bytes(out_path),
            sorted(os.listdir(self.tmpdir)),
        )

    def assert_sample_unchanged(self, db, out_path, before):
        """调用后现场与快照一致，且记录仍是固定样例原样。"""
        db_bytes, rows, out_bytes, listing = before
        self.assertEqual(rows, self.FIXED_ROWS, "样例准备后记录即应为固定内容")
        self.assertEqual(
            self.read_file_bytes(db), db_bytes, "源库文件字节必须保持不变"
        )
        self.assertEqual(
            self.read_flags_rows(db),
            self.FIXED_ROWS,
            "dev 的 yes 不得被修正，qa 的 true 必须原样保留",
        )
        self.assertEqual(out_bytes, b"KEEP", "out.json 预先保存的内容应为 KEEP")
        self.assertEqual(
            self.read_file_bytes(out_path), out_bytes, "已有 out.json 内容必须保留"
        )
        self.assertEqual(
            sorted(os.listdir(self.tmpdir)), listing, "临时目录不得新增文件"
        )

    def check_export_error_on_fixed_sample(self, env, code, output="out.json"):
        """在独立准备的固定样例上导出，核对唯一错误码与调用后现场不变。

        output 为 "out.json" 时输出到已存在的 out.json；为 "db" 时
        输出到 demo.sqlite 本身；为 "./db" 时输出到带 ./ 的等价路径。
        """
        db, out_path = self.make_fixed_sample()
        if output == "db":
            target = db
        elif output == "./db":
            target = os.path.join(os.path.dirname(db), ".", os.path.basename(db))
        else:
            target = out_path
        before = self.snapshot_sample(db, out_path)

        self.assertCommandError(self.export_flags(db, env, target), code)

        self.assert_sample_unchanged(db, out_path, before)

    def test_empty_env_reports_only_empty_env(self):
        self.check_export_error_on_fixed_sample("", "EMPTY_ENV")

    def test_spaces_only_env_reports_only_empty_env(self):
        self.check_export_error_on_fixed_sample("   ", "EMPTY_ENV")

    def test_tab_only_env_reports_only_empty_env(self):
        self.check_export_error_on_fixed_sample("\t", "EMPTY_ENV")

    def test_invalid_value_reports_storage_error_not_export_exists(self):
        # 目标环境合法键存有非法值且 out.json 已存在：完整读取配置
        # 先于输出文件检查，结果只能是 STORAGE_ERROR。
        self.check_export_error_on_fixed_sample("dev", "STORAGE_ERROR")

    def test_output_same_as_db_reports_storage_error_not_write_error(self):
        # 输出路径与库路径相同且目标环境存有非法值：读取配置先于
        # 输出路径检查，不能先报 EXPORT_WRITE_ERROR。
        self.check_export_error_on_fixed_sample("dev", "STORAGE_ERROR", output="db")

    def test_output_same_as_db_dot_prefix_reports_storage_error(self):
        # 带 ./ 的等价同库路径同样只报 STORAGE_ERROR。
        self.check_export_error_on_fixed_sample(
            "dev", "STORAGE_ERROR", output="./db"
        )


class TestExportWriteFailureCleanup(FlagctlCliTestCase):
    """export 在输出文件创建之后写入失败时的文件保护回归。

    固定样例库 demo.sqlite 保存 dev/new_ui=false 与 qa/new_ui=true，
    输出路径 dev.json 起初不存在。通过包装进程在 os.fdopen 返回的
    文件对象上注入 OSError，分别模拟两种失败：

    * empty：文件已创建但尚未写入任何内容时写入失败；
    * partial：已真实写入并落盘一部分非空内容后写入失败。

    两种情况都必须退出 2、stdout 为空、stderr 仅为 EXPORT_WRITE_ERROR
    加换行；调用结束后 dev.json 不存在，源库全部记录与文件字节、
    临时目录内原有的其他文件保持原样。注入点自身会校验失败确实发生
    在文件创建之后（partial 场景还校验确实产生过非空内容），否则以
    特殊退出码让用例明确失败，不允许用创建文件之前的失败代替。
    每个失败样例恢复正常写入条件后，立即用相同环境和输出路径重试
    导出：退出 0、stderr 为空，stdout 与生成文件同为单行
    {"new_ui": false} 加换行（无 BOM 的 UTF-8），qa 仍为 true，
    源库保持原样。失败条件只作用于本次导出的包装进程，不影响样例
    库准备、重试和其他用例。
    """

    # 包装脚本：以与 ``python flagctl.py --db ... export dev ...`` 等价
    # 的方式调用 flagctl.main()，仅把 os.fdopen 换成注入失败的版本。
    # 失败注入发生在 os.open 排他创建文件之后，因此异常必然出现在
    # 文件创建之后；脚本内对此自查，不满足时以 99/98 退出让用例失败。
    WRAPPER_TEMPLATE = '''import os
import sys

sys.path.insert(0, {here!r})
import flagctl

MODE = os.environ["FLAGCTL_TEST_FAIL_MODE"]
TARGET = os.environ["FLAGCTL_TEST_TARGET"]

real_fdopen = os.fdopen


class FailingWriter(object):
    def __init__(self, fh):
        self._fh = fh

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._fh.close()
        return False

    def write(self, data):
        # 自查：异常必须发生在文件创建之后，否则以退出码 99 让用例失败。
        if not os.path.isfile(TARGET):
            sys.exit(99)
        if MODE == "partial":
            # 先真实写入一部分字节并落盘，确认产生了非空内容后再失败；
            # 若文件仍为空，以退出码 98 让用例失败。
            self._fh.write(data[:5])
            self._fh.flush()
            if os.path.getsize(TARGET) == 0:
                sys.exit(98)
        raise OSError("simulated export write failure")


def failing_fdopen(fd, mode, *args, **kwargs):
    return FailingWriter(real_fdopen(fd, mode, *args, **kwargs))


os.fdopen = failing_fdopen

sys.exit(flagctl.main())
'''

    def make_demo_db(self):
        """固定样例库 demo.sqlite：dev/new_ui=false，qa/new_ui=true。"""
        db = os.path.join(self.tmpdir, "demo.sqlite")
        self.assertCommandOk(self.set_flag(db, "dev", "new_ui", "false"), "false")
        self.assertCommandOk(self.set_flag(db, "qa", "new_ui", "true"), "true")
        return db

    def write_wrapper(self, mode):
        """在临时目录写入失败注入包装脚本并返回路径。"""
        wrapper = os.path.join(self.tmpdir, "failing_export_" + mode + ".py")
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write(self.WRAPPER_TEMPLATE.format(here=HERE))
        return wrapper

    def run_failing_export(self, wrapper, db, path, mode):
        """在包装进程中执行 export dev dev.json，注入指定模式的写入失败。"""
        env = dict(os.environ)
        env["FLAGCTL_TEST_FAIL_MODE"] = mode
        env["FLAGCTL_TEST_TARGET"] = path
        return subprocess.run(
            [sys.executable, wrapper, "--db", db, "export", "dev", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def check_write_failure_then_retry(self, mode):
        """文件创建后写入失败 -> 清理残缺文件 -> 恢复正常后重试成功。"""
        db = self.make_demo_db()
        path = os.path.join(self.tmpdir, "dev.json")
        self.assertFalse(os.path.exists(path))
        # 临时目录内原有的其他文件：失败与重试都不得改动它。
        sentinel = os.path.join(self.tmpdir, "keep.txt")
        with open(sentinel, "wb") as fh:
            fh.write(b"untouched\n")
        wrapper = self.write_wrapper(mode)
        with open(db, "rb") as fh:
            db_bytes_before = fh.read()
        rows_before = self.read_flags_rows(db)
        listing_before = sorted(os.listdir(self.tmpdir))

        proc = self.run_failing_export(wrapper, db, path, mode)

        # 退出 2、stdout 为空、stderr 仅为 EXPORT_WRITE_ERROR 加换行；
        # 若注入发生在文件创建之前（或未产生非空内容），包装进程会以
        # 99/98 退出，下面的断言随之失败。
        self.assertCommandError(proc, "EXPORT_WRITE_ERROR")
        self.assertFalse(
            os.path.exists(path), "写入失败后不得遗留本次新建的残缺文件"
        )
        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), db_bytes_before, "源库字节必须保持不变")
        self.assertEqual(self.read_flags_rows(db), rows_before, "源库记录必须保持不变")
        with open(sentinel, "rb") as fh:
            self.assertEqual(fh.read(), b"untouched\n", "其他文件必须保持原样")
        self.assertEqual(
            sorted(os.listdir(self.tmpdir)),
            listing_before,
            "临时目录内不得新增或丢失其他文件",
        )

        # 恢复正常写入条件，立即用相同环境和输出路径重试导出。
        retry = self.export_flags(db, "dev", path)
        self.assertCommandOk(retry, '{"new_ui": false}')
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "输出文件不得带 UTF-8 BOM")
        self.assertEqual(raw, b'{"new_ui": false}\n')
        self.assertCommandOk(self.get_flag(db, "qa", "new_ui"), "true")
        with open(db, "rb") as fh:
            self.assertEqual(fh.read(), db_bytes_before, "重试后源库字节必须保持不变")
        self.assertEqual(self.read_flags_rows(db), rows_before, "重试后源库记录必须保持不变")

    def test_write_error_before_any_content_removes_new_file(self):
        # 文件已创建但尚未写入内容时发生 OSError。
        self.check_write_failure_then_retry("empty")

    def test_write_error_after_partial_content_removes_new_file(self):
        # 已写入部分内容后发生 OSError。
        self.check_write_failure_then_retry("partial")


if __name__ == "__main__":
    unittest.main()