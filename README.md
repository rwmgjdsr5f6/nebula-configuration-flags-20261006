# flagctl：本地功能开关入门

flagctl.py 是一个本地单机的命令行工具，按“环境 + 键”保存和读取布尔型
功能开关（feature flags）。所有数据都在你自己指定的一个 SQLite 文件里。

## 运行环境

- 只需 Python 3（仅用 argparse、json、sqlite3 等标准库）以及 Python
  自带的 SQLite 支持；
- 不访问网络，不需要安装、也不依赖任何第三方包；
- 每次调用通过 `--db <数据库文件>` 选择本次使用的本地配置库，不同库
  文件之间互不相干。

基本形式：

    python flagctl.py --db <数据库文件> <命令> ...

## 固定样例：第一次使用

样例的固定前提：数据库文件的父目录（例如当前工作目录）已经存在，而
`demo.sqlite` 尚不存在。

第一步，写入一个开关：

    python flagctl.py --db demo.sqlite set dev new_ui false

第二步，把它读回来：

    python flagctl.py --db demo.sqlite get dev new_ui

预期：两条命令均退出 0；stdout 都只有一行 `false`（末尾带换行），
stderr 均为空。

两步之间发生的事：

- 第一次执行 `set` 时才创建 `demo.sqlite` 文件和其中的 `flags` 表，
  并写入一行持久化记录；
- 第二次执行 `get` 不创建任何文件或表，只是从 SQLite 中把上一步
  保存的记录读出来；读取不经过任何网络服务或进程内缓存。

（以上为对照源码可验证的预期输出，本文不声称样例已经实际执行。）

## 命令一览

    python flagctl.py --db <库> set <环境> <键> <true|false>
    python flagctl.py --db <库> get <环境> <键>
    python flagctl.py --db <库> unset <环境> <键>
    python flagctl.py --db <库> list <环境>
    python flagctl.py --db <库> diff <环境左> <环境右>

- **set**：保存或覆盖一个直接设置，成功时 stdout 回显所写入的
  `true` / `false`。
- **get**：读取一个直接设置，stdout 为 `true` 或 `false`。
- **unset**：删除该环境该键的直接设置（删除整行记录，而不是写入
  `false`）；成功时 stdout 为 `unset`。
- **list**：输出该环境所有已保存开关组成的 JSON 对象，值为 JSON
  布尔值，例如 `{"new_ui": false}`；没有任何已保存开关时输出 `{}`。
- **diff**：按参数顺序比较两个环境的直接设置，只输出两侧不同的键，
  例如 `{"new_ui": {"left": false, "right": null}}`；一侧未设置用
  JSON `null` 表示。两侧都没有任何设置（或所有已知键两侧相同）时
  输出 `{}`。

关于“直接设置为 false”与“未设置（null）”的区别，以及 unset、diff
在这条流程上的完整行为，见 [direct-setting.txt](direct-setting.txt)。
请特别注意：**键缺失只表示“没有直接设置”，不应被解释为默认 false。**

## 输入规则

- **合法键**：当前只有 `new_ui` 一个（源码中的 KNOWN_KEYS），写入或
  查询其他键名都会被拒绝。
- **布尔值**：set 只接受严格小写的文本 `true` 或 `false`；`TRUE`、
  `1`、`yes`、带空白等写法一律不接受，不做大小写或空白容错。
- **环境名**：去除两端空白后使用，内部空白原样保留，并且区分大小写
  （`Dev` 与 `dev` 是两个互不相同的环境）。

## SQLite 数据格式

每个 `--db` 指向一个普通 SQLite 文件，其中有一张结构固定的表：

    CREATE TABLE flags (
        env   TEXT NOT NULL,
        key   TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (env, key)
    )

- value 列只保存严格文本 `true` 或 `false`；
- 同一 (env, key) 再次 set 会覆盖旧行（INSERT OR REPLACE）；
- **set 首次写入时**会创建缺失的数据库文件并用
  `CREATE TABLE IF NOT EXISTS` 建表；
- **get、unset、list、diff 不补建任何文件或表**：库文件不存在或缺
  flags 表时按下文的错误/空结果规则处理。

## 输出协议

- **成功**：退出码 0；stderr 为空；stdout 为单行结果加换行。
  set/get 输出布尔文本，unset 输出 `unset`，list/diff 输出一行
  JSON 对象。
- **失败**：退出码 2；stdout 为空；stderr 仅为错误码加换行，
  例如 `VALUE_NOT_SET`，不附带其他文本。

## 错误码

- **EMPTY_ENV**：环境名为空，或去除两端空白后为空。diff 的两个环境
  名都会在访问存储之前校验，任一为空即报此错。
- **UNKNOWN_KEY**：键名不是当前合法键（`new_ui`）。
- **INVALID_BOOL**：set 给出的值不是严格小写的 `true` / `false`。
- **VALUE_NOT_SET**：get 或 unset 的目标 (环境, 键) 没有已保存的
  记录。父目录存在但数据库文件缺失、或文件是有效 SQLite 库但缺少
  flags 表时，也按此处理——查询不会顺手补建文件或表。
- **STORAGE_ERROR**：父目录不存在；目标文件不是有效的 SQLite 数据库；
  操作所需的表列缺失；以及 get、list、diff 在目标范围内读到已知键
  保存了 `true` / `false` 之外的非法值（数据损坏）。

校验顺序与副作用：

- set 严格按 **环境名 → 键名 → 布尔文本** 的顺序校验，任一失败都在
  访问存储之前拒绝，因此输入校验失败不会创建数据库文件或表。
- list 不把“没有数据”当作错误：数据库文件缺失、有效库缺少 flags 表
  或该环境没有任何记录，都输出 `{}`。
- diff 的两侧各自按 list 的规则处理：两侧均无设置时输出 `{}`；任一
  目标环境中的已知键存在非法存储值则报 STORAGE_ERROR，不输出部分
  差异。其他环境以及未知键的记录不在目标范围内，其内容不影响结果。

## 当前不提供的能力

- **没有默认值**：键不存在就是未设置，系统不会替它补成 false；
- **没有环境继承**：环境之间相互独立，不存在 dev 继承另一环境之类
  的层级关系；
- **没有变更历史**：覆盖与删除都不保留旧值，无法追溯改动记录。
