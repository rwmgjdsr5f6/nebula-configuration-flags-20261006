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

    python flagctl.py --db <库> set <环境> <键> <true|false> [--dry-run]
    python flagctl.py --db <库> get <环境> <键>
    python flagctl.py --db <库> unset <环境> <键> [--dry-run]
    python flagctl.py --db <库> list <环境>
    python flagctl.py --db <库> diff <环境左> <环境右> [--exit-code]
    python flagctl.py --db <库> envs
    python flagctl.py --db <库> import <环境> <JSON 文件>
    python flagctl.py --db <库> export <环境> <JSON 文件>

- **set**：保存或覆盖一个直接设置，成功时 stdout 回显所写入的
  `true` / `false`。附加 `--dry-run` 时完全不写入，无需 JSON 文件
  即可只读预览这一个键将发生的变化：stdout 为单行 JSON 对象，形如
  `{"new_ui":{"before":true,"after":false}}`，`before` 是目标记录的
  原值（未设置用 `null`），`after` 是命令行给出的目标布尔值；原值与
  目标值相同（含已保存的 `false` 再次设为 `false`）时输出 `{}`。
  `false` 是有效设置，与未设置明确区分，预览不补默认值或继承值。
  预览严格按环境名 → 键名 → 布尔文本的顺序校验，全部通过后才读取
  存储（错误码与正式 set 一致）；库文件缺失但父目录存在、有效库缺
  flags 表或目标记录缺失时原值视为 `null`，父目录缺失、无效库、查询
  所需列缺失或目标记录值不是严格文本 `true`/`false` 时报
  STORAGE_ERROR，不返回部分结果；其他环境和未知键记录不影响预览。
  预览全程不创建目录、库文件或表，也不改动任何记录，重复调用得到
  相同结果。
- **get**：读取一个直接设置，stdout 为 `true` 或 `false`。
- **unset**：删除该环境该键的直接设置（删除整行记录，而不是写入
  `false`）；成功时 stdout 为 `unset`。附加 `--dry-run` 时完全不删除，
  只在正式删除前只读预览这一个键将发生的变化：stdout 为单行紧凑 JSON
  对象，形如 `{"new_ui":{"before":false,"after":null}}`，`before` 是目标
  行当前的严格文本值（`true` / `false` 映射为同名 JSON 布尔值），
  `after` 固定为 `null` 表示撤销后该行不存在；原值 `false` 与 `true`
  一样输出变化，不被当作未设置，预览也不补默认值或继承值。预览严格按
  环境名 → 键名的顺序校验（unset 不接收布尔值参数），全部通过后才读取
  存储（错误码与正式 unset 一致）；父目录存在但库文件缺失、有效库缺
  flags 表或目标行不存在报 VALUE_NOT_SET，父目录缺失、无效库、查询所需
  列缺失、连接或查询失败或目标行值不是严格文本 `true`/`false` 时报
  STORAGE_ERROR，不输出部分结果；其他环境和未知键记录不影响预览。预览
  全程不创建目录、库文件或表，也不改动记录或修复异常值，重复调用得到
  相同结果。
- **list**：输出该环境所有已保存开关组成的 JSON 对象，值为 JSON
  布尔值，例如 `{"new_ui": false}`；没有任何已保存开关时输出 `{}`。
- **diff**：按参数顺序比较两个环境的直接设置，只输出两侧不同的键，
  例如 `{"new_ui": {"left": false, "right": null}}`；一侧未设置用
  JSON `null` 表示。两侧都没有任何设置（或所有已知键两侧相同）时
  输出 `{}`。附加 `--exit-code` 时 stdout/stderr 协议不变，但退出码
  额外表达比较结果：差异对象为空退出 0、非空退出 1；不加该参数时
  无论有无差异都退出 0。两种情况下比较失败仍退出 2（见“输出协议”）。
- **envs**：输出库中“已有直接设置的环境”组成的 JSON 数组，例如
  `["dev", "qa"]`。环境含有至少一个合法键（`new_ui`）的直接设置时
  才出现，直接设置为 `false` 也算已设置；只有未知键记录的环境不
  出现，未知键的异常值也不影响结果。同名环境去重，按名称的 Unicode
  码点字典序升序排列；名称按库中保存的文本原样输出（区分大小写、
  保留空白，`Dev` 与 `dev` 分开列出）。库文件缺失、有效库缺 flags
  表或没有合法键记录时输出 `[]`。envs 为只读命令，不补建目录、
  文件或表。
- **import**：从本地 JSON 文件导入一个环境的直接设置。文件采用
  UTF-8，内容与 list 输出相同（顶层为对象，键是开关名、值是 JSON
  布尔值），例如 `{"new_ui": false}`。导入覆盖目标环境中出现的
  同名键，保留其他环境和文件中未出现的记录；`false` 是实际设置，
  不代表删除。成功时 stdout 为仅含本次导入项的单行 JSON 对象；
  空对象 `{}` 直接成功返回 `{}`，不访问数据库。非空导入沿用 set
  的存储行为：创建缺失的数据库文件和 flags 表，但不创建父目录。
  附加 `--dry-run` 时完全不写入，只在正式写入前只读预览变化：
  stdout 为只含变化键的单行 JSON 对象，每项形如
  `{"new_ui":{"before":true,"after":false}}`，`before` 是目标环境
  的原值（未设置用 `null`），`after` 是文件中的导入值；相同值不
  输出，文件未提及的键保持原样、不表示删除。校验顺序与错误码和
  普通导入一致（环境名 → 读文件 → JSON 与键/值校验，全部通过后
  才读取存储）；库文件缺失但父目录存在、有效库缺 flags 表或目标
  环境无设置时原值视为 `null`，父目录缺失、无效库、所需列缺失或
  目标环境合法键的存储值不是严格文本 `true`/`false` 时报
  STORAGE_ERROR。预览全程不创建目录、库文件或表，也不改动任何
  记录和输入文件；合法空对象直接返回 `{}`，不访问数据库。
- **export**：把一个环境已保存的直接设置导出为单环境 JSON 文件。
  导出对象与 list 输出一致：仅含目标环境已保存的合法键，值为
  JSON 布尔值；`false` 原样保留，未设置的键不出现，不补默认值或
  继承值。文件为无 BOM 的 UTF-8，内容是单行 JSON 对象加换行，
  成功时 stdout 输出同一份文本。库文件缺失、有效库缺 flags 表或
  环境无合法设置时导出 `{}`（不创建数据库或表）；目标环境的合法
  键存有非法值等存储异常报 STORAGE_ERROR。输出文件已存在时报
  EXPORT_EXISTS 并保留原内容；输出路径与库路径相同、输出父目录
  缺失或写入失败报 EXPORT_WRITE_ERROR。导出全程只读源库。

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
- **set 首次写入时**（以及非空 import 首次导入时）会创建缺失的
  数据库文件并用 `CREATE TABLE IF NOT EXISTS` 建表；
- **get、unset、list、diff、envs 以及 set/import/unset 的 --dry-run 预览不
  补建任何文件或表**：库文件不存在或缺 flags 表时按各自的错误/空结果
  规则处理。

## 输出协议

- **成功**：退出码 0；stderr 为空；stdout 为单行结果加换行。
  set/get 输出布尔文本（set 附加 `--dry-run` 时为例外，输出紧凑的
  单行 JSON 变化对象），unset 输出 `unset`（附加 `--dry-run` 时同为
  例外，输出紧凑的单行 JSON 变化对象），list/diff 输出一行
  JSON 对象，envs 输出一行 JSON 数组。唯一的例外是 diff 附加
  `--exit-code`：stdout/stderr 仍是同一份单行 JSON 与空 stderr，但
  完整比较发现差异（差异对象非空）时退出 1，差异对象为空时仍退出 0。
- **失败**：退出码 2；stdout 为空；stderr 仅为错误码加换行，
  例如 `VALUE_NOT_SET`，不附带其他文本。diff `--exit-code` 的退出码
  1 只表示“完整比较发现差异”，不表示失败；任一比较失败仍退出 2。

## 错误码

- **EMPTY_ENV**：环境名为空，或去除两端空白后为空。diff 的两个环境
  名都会在访问存储之前校验，任一为空即报此错；import 在读文件之前
  校验环境名。
- **UNKNOWN_KEY**：键名不是当前合法键（`new_ui`）。import 在解析
  文件后先校验全部键名。
- **INVALID_BOOL**：set 给出的值不是严格小写的 `true` / `false`；
  或 import 文件中的值不是 JSON 布尔值（如 `"false"`、`1`、`null`）。
- **IMPORT_READ_ERROR**：import 的文件不存在或无法读取。
- **INVALID_JSON**：import 的文件不是合法 UTF-8、JSON 语法错误、
  顶层不是对象或存在重复键。未加引号的 `NaN`、`Infinity`、
  `-Infinity` 不是合法 JSON 词法（出现在任意嵌套位置都算语法错误，
  先于键名与布尔值校验）；引号内的同名文本只是普通字符串。
- **VALUE_NOT_SET**：get、unset 或 unset `--dry-run` 的目标
  (环境, 键) 没有已保存的记录。父目录存在但数据库文件缺失、或文件是
  有效 SQLite 库但缺少 flags 表时，也按此处理——查询不会顺手补建文件
  或表。
- **STORAGE_ERROR**：父目录不存在；目标文件不是有效的 SQLite 数据库；
  操作所需的表列缺失；以及 get、list、diff、envs、export 和
  set/unset --dry-run 在目标范围内读到已知键保存了 `true` / `false`
  之外的非法值（数据损坏）。envs 的目标范围是全库：库中任一合法键的
  值非法都报此错，不输出部分名单。
- **EXPORT_EXISTS**：export 的输出文件已存在；原内容保留，不被覆盖。
- **EXPORT_WRITE_ERROR**：export 的输出路径与数据库路径相同、输出
  父目录不存在、无法写入或写入失败。不创建目录，失败不遗留新增的
  残缺文件。

校验顺序与副作用：

- set 严格按 **环境名 → 键名 → 布尔文本** 的顺序校验，任一失败都在
  访问存储之前拒绝，因此输入校验失败不会创建数据库文件或表；附加
  `--dry-run` 时校验顺序与错误码不变，只是在全部校验通过后只读查询
  原值并输出变化，绝不写入。
- unset 严格按 **环境名 → 键名** 的顺序校验（不接收布尔值参数），
  任一失败都在访问存储之前拒绝，因此输入校验失败不会访问或创建数据库
  文件或表；附加 `--dry-run` 时校验顺序与错误码不变，只是在全部校验
  通过后只读查询原值并输出变化，绝不删除或改动任何记录。
- import 严格按 **环境名 → 读文件 → JSON 解析 → 全部键名 → 全部
  布尔值** 的顺序校验，全部通过后才访问数据库；校验失败不会创建
  数据库或表，也不会改动任何记录。非空导入在一个事务中写入，遇到
  父目录不存在、无效数据库、flags 表缺少所需列或写入失败时报
  STORAGE_ERROR，既有记录保持原样。
- list 不把“没有数据”当作错误：数据库文件缺失、有效库缺少 flags 表
  或该环境没有任何记录，都输出 `{}`。
- envs 同样不把“没有数据”当作错误：数据库文件缺失、有效库缺少
  flags 表或全库没有任何合法键记录，都输出 `[]`。它全程只读，且
  不接收环境名或键名；库中任一合法键存有非法值则报 STORAGE_ERROR，
  不输出部分名单。
- diff 的两侧各自按 list 的规则处理：两侧均无设置时输出 `{}`；任一
  目标环境中的已知键存在非法存储值则报 STORAGE_ERROR，不输出部分
  差异。其他环境以及未知键的记录不在目标范围内，其内容不影响结果。
- export 严格按 **环境名 → 完整读取配置 → 输出文件** 的顺序处理：
  配置读取规则与 list 相同（无数据导出 `{}`，目标环境合法键的非法
  值报 STORAGE_ERROR）；读取全部通过后才检查输出文件——路径与
  数据库路径相同报 EXPORT_WRITE_ERROR，已存在报 EXPORT_EXISTS 并
  保留原内容，随后排他创建写入。任何失败都不创建或改动输出文件，
  也不改动源库。

## 当前不提供的能力

- **没有默认值**：键不存在就是未设置，系统不会替它补成 false；
- **没有环境继承**：环境之间相互独立，不存在 dev 继承另一环境之类
  的层级关系；
- **没有变更历史**：覆盖与删除都不保留旧值，无法追溯改动记录。
