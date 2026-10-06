# flagctl 本地功能开关入门

flagctl 是一个本地单机的布尔功能开关命令行工具：在你指定的 SQLite
配置库里，按“环境 + 键”直接保存 `true` / `false`，并提供读取、撤销、
列举和两个环境之间的差异比较。

## 运行要求

- Python 3，只使用标准库（`argparse`、`sqlite3`、`json` 等）；
- SQLite 由 Python 自带的 `sqlite3` 模块提供，不需要安装或启动任何
  数据库服务；
- 不访问网络，不依赖第三方包，无需安装，在本目录直接运行：

  ```
  python flagctl.py --db <数据库文件> <命令> ...
  ```

`--db` 选择你自己的本地配置库路径；换一个路径就是一个互不影响的独立
配置库。

## 快速开始（固定样例）

前置状态固定为：数据库文件的父目录（即当前工作目录）已经存在，而
`demo.sqlite` 尚不存在。

第一步，写入一条直接设置：

```
python flagctl.py --db demo.sqlite set dev new_ui false
```

预期：退出码 0，stderr 为空，stdout 仅为 `false` 加末尾换行。
这是 `set` 第一次成功执行，会创建 `demo.sqlite` 文件和其中的
`flags` 表，并写入一行 `(env='dev', key='new_ui', value='false')`。

第二步，把它读回来：

```
python flagctl.py --db demo.sqlite get dev new_ui
```

预期：退出码 0，stderr 为空，stdout 同样仅为 `false` 加末尾换行。
`get` 是纯只读操作，这里读到的是上一条命令持久化到 SQLite 中的记录，
而不是内存状态，也不是任何默认值。

以上输出是按当前源码推导的可验证预期，本指南没有宣称样例已经实际执行
通过。`*.sqlite` 已在 `.gitignore` 中忽略，`demo.sqlite` 不会进入
版本控制。

## 命令

| 命令 | 含义 | 成功时 stdout |
| --- | --- | --- |
| `set <环境> <键> <true\|false>` | 写入或覆盖一条直接设置；首次成功执行时创建库文件和 `flags` 表 | 回显所设值，即 `true` 或 `false` |
| `get <环境> <键>` | 读取一条直接设置 | `true` 或 `false` |
| `unset <环境> <键>` | 删除该（环境，键）的直接设置记录，而不是改成 `false` | `unset` |
| `list <环境>` | 列出该环境下已保存开关的 JSON 对象，值为 JSON 布尔值 | 如 `{"new_ui": false}`；没有任何设置时为 `{}` |
| `diff <左环境> <右环境>` | 比较两侧的直接设置，只输出两侧不同的键；未设置序列化为 `null` | 如 `{"new_ui": {"left": false, "right": null}}`；两侧都没有设置时为 `{}` |

`diff` 的左右由参数顺序决定；两侧同为 `true`、同为 `false` 或同为
未设置的键都不输出。`list` 和 `diff` 只输出已知键的记录，未知键的行
被跳过。

## 输入规则

- **合法键**：当前只有 `new_ui` 一个。其他键名一律报 `UNKNOWN_KEY`。
- **布尔值**：写入只接受严格的小写文本 `true` 或 `false`。
  `TRUE`、`True`、`1`、`false `（带空白）等都不是合法输入，报
  `INVALID_BOOL`，不做大小写或空白容错。
- **环境名**：去除两端空白后使用，内部空白原样保留，并且区分大小写。
  例如 `" Dev "` 变为 `Dev`，`"prod eu"` 保留中间空格，`Dev` 与
  `dev` 是两个不同的环境。

## SQLite 数据格式

配置库就是一个普通 SQLite 文件，其中只有一张表，结构保持不变：

```sql
CREATE TABLE IF NOT EXISTS flags (
    env   TEXT NOT NULL,
    key   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (env, key)
)
```

每个（环境，键）至多一行；`value` 列保存严格的文本 `true` 或
`false`。直接用其他工具写库时也应遵守这一格式。

## 输出协议

- 成功：退出码 0，stderr 为空，stdout 为单行结果加换行。
  `list` / `diff` 的结果是一行 JSON 对象；`get` 输出 `true` 或
  `false`；`set` 回显所设值；`unset` 输出 `unset`。
- 失败：退出码 2，stdout 为空，stderr 仅为错误码加换行（如
  `VALUE_NOT_SET\n`），不附带其他文本。

## 错误处理

| 错误码 | 触发情况 |
| --- | --- |
| `EMPTY_ENV` | 环境名为空，或去除两端空白后为空；`diff` 的任一环境名为空同样先报此错 |
| `UNKNOWN_KEY` | 键名不是当前已知键（仅 `new_ui`） |
| `INVALID_BOOL` | `set` 的值不是严格小写的 `true` / `false` |
| `VALUE_NOT_SET` | `get` 或 `unset` 的目标（环境，键）没有已保存的记录；父目录存在但数据库文件缺失；数据库有效但缺少 `flags` 表 |
| `STORAGE_ERROR` | 数据库文件的父目录不存在；文件存在但不是有效的 SQLite 库；`flags` 表缺少操作所需的列；`get`、`list`、`diff` 在目标范围内读到已知键的非法存储值（即不是严格文本 `true` / `false`） |

补充说明：

- `set` 的校验顺序固定为**环境名 → 键名 → 布尔值**（先 `EMPTY_ENV`，
  再 `UNKNOWN_KEY`，最后 `INVALID_BOOL`），任一校验失败都在访问存储
  之前拒绝，**不会创建配置库文件或表**。
- 只有成功执行 `set` 才会创建库文件和 `flags` 表；`get`、`unset`、
  `list`、`diff` 全程只读/按主键删除，**不会补建缺失的文件或表**。
- 对 `list` 和 `diff` 而言，“库文件缺失”“有效库缺少 `flags` 表”
  “该环境没有任何记录”都表示没有已保存的设置：`list` 输出 `{}`，
  `diff` 把对应一侧视为空；两侧均无设置时 `diff` 输出 `{}`。
- 非法存储值的检查范围仅限本次操作的目标：`get` 只检查目标记录，
  `list` 检查目标环境的已知键行，`diff` 检查左右两个环境；其他环境
  或未知键行里的异常值不影响这些命令。

## 当前边界

flagctl 目前只管理显式的直接设置：

- **没有默认值**：键缺失只表示“没有直接设置”，不应被解释为默认
  `false`；
- **没有环境继承**：各环境的记录相互独立，`diff` 只做字面比较；
- **没有变更历史**：`set` 覆盖旧值、`unset` 删除记录，均不保留历史。

特别地，**直接设置为 `false` 与未设置是两种不同状态**：`false` 是一行
真实保存的记录，未设置是行不存在；二者在 `diff` 中也不相等
（`false` 对 `null` 会被报告为差异）。完整说明和固定样例见
[`direct-setting.txt`](direct-setting.txt)。
