# CryptoLab — 本地实验记录与管理

CryptoLab 是面向密码分析科研的本地实验记录 SDK，保存参数、结构化结果、时间、状态、日志和附件。它不执行或调度实验，也不包含密码算法或建模代码。Python >=3.11，核心仅使用标准库；Streamlit、Gurobi 和 pytest 均为可选依赖。

当前版本 **0.3.0**，已实现 [需求文档](docs/requirements.md) 的第一至第三阶段：SDK、并发可靠性与 Streamlit 管理界面。原有记录 API、连接管理、日志隔离和心跳机制保留，管理页面复用现有数据库事务层。数据库继续使用 schema v1，不删除重建历史记录。原始需求文件及其副本未改动；模块设计与验证记录见 [设计文档](docs/design.md)。

## 安装

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
# SDK 测试依赖：
python -m pip install -e '.[test]'
# 可选：管理界面和全部测试依赖
python -m pip install -e '.[web,test]'
python -m pytest -q
```

也可以使用已有的 Conda 环境（Python >=3.11）：

```bash
conda activate YOUR_ENV
python -m pip install -e /path/to/CryptoLab
```

在每个需要调用 SDK 的 Python/Conda 环境中安装即可。各环境可通过同一个 `CRYPTOLAB_HOME` 使用同一份本机数据。核心包不需要启动服务，也不导入 `gurobipy`。已有 Gurobi 环境可以直接使用；需要安装其 Python 包时可选择 `python -m pip install -e '.[gurobi]'`，许可证另行配置。

## 启动本地管理界面

macOS 上可为管理界面使用独立的 `cryptolab` Conda 环境。首次配置时在项目根目录执行一次（若环境已存在，先检查并直接激活，不要删除重建）：

```bash
conda create -n cryptolab python=3.12 -y
conda activate cryptolab
python -m pip install -e '.[web,test]'
python -m pip check
python -m pytest -q
```

配置完成后，在项目根目录执行 `./start.sh`。本机也可以从任意目录使用一条命令启动：

```bash
"/Users/single/Desktop/小工具/start.sh"
```

脚本自动进入自身所在的项目目录，在脚本进程内激活 `cryptolab` 环境，使用 `127.0.0.1:8501` 启动 Streamlit；不需要提前执行 `conda activate`，也不会重复创建环境或安装依赖。若 8501 端口被占用，脚本会明确报错退出，不终止已有进程、不自动换端口。终端会持续显示运行日志，按 **Ctrl+C** 停止服务；只关闭浏览器标签页不会停止服务。

脚本沿用已有的 `CRYPTOLAB_HOME` 设置；未设置时仍使用 `~/.cryptolab/`，数据与 Python 环境分开保存。此次安装仅选择 `web,test`，不需要在管理界面环境中安装 Gurobi 或 SageMath。源码以可编辑模式安装，移动项目目录后需要重新安装可编辑包。

也可以手动启动：

在仓库根目录和已安装 CryptoLab 的环境中执行：

```bash
python -m pip install -e '.[web]'
python -m streamlit run web/app.py --server.address 127.0.0.1
```

浏览器打开 `http://127.0.0.1:8501`。关闭界面不影响 SDK；不同 Conda 环境只要使用相同 `CRYPTOLAB_HOME`，就能查看同一份数据。仓库配置默认只监听回环地址并关闭使用统计，页面也会拒绝非回环地址配置。没有账号、云同步或远程实验接口。从其他目录启动时，传入 `web/app.py` 的绝对路径，并保留 `--server.address 127.0.0.1`。

侧栏提供四个页面：

| 页面 | 操作 |
| --- | --- |
| 实验列表 | 自动与手动记录统一展示；按项目、方案、Batch、轮数、开始日期、运行状态、求解状态、来源、算法和关键词筛选；可每 5 秒刷新；导出当前筛选的 CSV/JSON。 |
| 项目管理 | 创建、重命名项目和实验方案，填写说明、方法及算法标签；查看方案内 Run 与批次汇总。 |
| 实验详情 | 查看参数、结果、时间、复现信息、日志、附件和修改历史；人工修订、软删除/恢复、异常结束确认、附件补录。 |
| 手动录入 | 为历史实验填写 JSON 参数/结果、真实时间、状态和备注，上传已有结果文件或日志；允许时间未知。 |

默认显示时区为 `Asia/Shanghai`，可填写其他 IANA 时区。详情同时展示本地时间和 UTC 原值；日期筛选按选定时区换算，包含结束日期全天。历史时间未知的记录不会匹配开始日期筛选。时间输入为空表示未知；不带偏移量的输入按显示时区解释，夏令时歧义时间要求明确偏移量。

CSV 使用 UTF-8 BOM，参数/指标保存为 JSON 单元格；可能被表格程序解释为公式的字符串增加前导单引号。JSON 导出保留数据类型、UTC 时间和空值，适合后续 Python 分析。导出范围与当前列表一致，不包含附件内容；附件单独下载。

## 数据位置

默认目录为 `~/.cryptolab/`，可在运行前指定：

```bash
export CRYPTOLAB_HOME="$HOME/cryptolab-data"
```

```text
$CRYPTOLAB_HOME/
├── cryptolab.db
└── runs/
    └── <run-uuid>/
        ├── run.log
        ├── stdout.log       # 显式开启捕获时
        ├── stderr.log       # 显式开启捕获时
        ├── traceback.txt    # 失败或中断时，尽力写入
        └── result.json
```

使用本机文件系统；不要把 WAL 数据库放在网络共享文件系统。源码与记录数据分开存放。自动测试全部设置临时 `CRYPTOLAB_HOME`，不会写入真实默认目录。

## 完整单次示例

以下是普通 Python 模拟实验，不依赖 Gurobi，也不使用或修改已有密码分析代码：

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from cryptolab import track_run

with TemporaryDirectory() as temporary:
    source = Path(temporary) / "input.txt"
    source.write_text("模拟实验输入\n", encoding="utf-8")

    with track_run(
        project="CryptoLab 示例",
        experiment="单次模拟实验",
        params={"rounds": 12, "time_limit": 300},
        capture_console=True,
    ) as run:
        print("开始模拟实验")
        run.log_params({"seed": 42})
        result = {"satisfiable": True, "weight": 24, "trail": [1, 2, 4]}
        run.log_metrics(result)
        run.log("模拟实验完成")
        run.output_path("result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run.add_file(source)

    print("Run ID:", run.id)
```

直接执行：`python examples/single_run.py`。上下文进入时写入 `RUNNING`，正常退出时更新为 `COMPLETED`。每次执行都创建新 UUID，即使参数相同也不会覆盖旧记录。

## 完整批量示例

```python
from cryptolab import track_batch, track_run

with track_batch(
    project="CryptoLab 示例",
    experiment="批量模拟实验",
    name="8–12 轮测试",
    common_params={"time_limit": 300, "method": "SAT-like"},
) as batch:
    for rounds in range(8, 13):
        with track_run(batch_id=batch.id, params={"rounds": rounds}) as run:
            run.log(f"正在模拟 {rounds} 轮实验")
            run.log_metrics({"satisfiable": True, "weight": 2 * rounds})
            run.output_path("result.txt").write_text(
                f"rounds={rounds}, weight={2 * rounds}\n", encoding="utf-8"
            )
            print("Run ID:", run.id)

print("Batch ID:", batch.id)
```

直接执行：`python examples/batch_run.py`。得到 1 Batch 和 5 Run。每条 Run 保存完整有效参数，例如 `{"time_limit": 300, "method": "SAT-like", "rounds": 8}`。参数按顶层 key 合并，单次参数覆盖同名公共参数，嵌套字典整体替换，不递归合并。

Batch 的持久化状态继续表示其 `with` 作用域是否正常结束；`get_batch_summary(batch.id)` 另外统计子 Run，不覆盖批次或子 Run 的状态。捕获子 Run 异常后继续执行时，Batch 可以是 `COMPLETED`，该子 Run 仍为 `FAILED`。批次不负责等待用户自行启动的后台任务。

## 多进程与多线程调用示例

多个终端、`multiprocessing.Process`、`ProcessPoolExecutor` 或 `ThreadPoolExecutor` 可同时写同一个本机数据目录。每个 worker 自行创建 Run，仅传 Batch ID；不要跨进程传递 Run、连接或 Gurobi 模型。Run、Batch 和 Database 对象拒绝序列化，并检查所属进程。进程示例显式使用 `spawn`，兼容 macOS；worker 定义在模块顶层，启动代码放在 `if __name__ == "__main__"` 中。SDK 会拒绝在继承活跃 Run 的 fork 子进程中开始新 Run。

```python
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from multiprocessing import get_context
from cryptolab import get_batch_summary, track_batch, track_run

def worker(rounds, batch_id):
    with track_run(batch_id=batch_id, params={"rounds": rounds}) as run:
        run.log(f"模拟 {rounds} 轮")
        run.log_metrics({"weight": rounds * 2})
        return run.id

if __name__ == "__main__":
    with track_batch(project="示例", experiment="进程示例") as batch:
        with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
            run_ids = list(pool.map(worker, range(8, 48), repeat(batch.id)))
    print("Run 数量:", len(run_ids))
    print(get_batch_summary(batch.id))
```

线程使用相同的 `worker`，将执行器替换为 `ThreadPoolExecutor(max_workers=4)`，不传 `mp_context`。线程间使用各自的 `run.log()`，不要开启 `capture_console`。现成示例分别记录 40 条和 60 条 Run，可直接运行：

```bash
python examples/multiprocess_run.py
python examples/multithread_run.py
```

若使用低层接口，也可通过 `context = multiprocessing.get_context("spawn")` 创建 `context.Process(target=worker, args=(rounds, batch.id))`；父进程应 `start()`、`join()` 并检查 `exitcode`。不同终端各自设置相同的 `CRYPTOLAB_HOME` 即可，不需要共同的父进程。

数据库每次读写都在当前进程/线程新建并关闭连接，不关闭 SQLite 默认的线程检查。写事务在访问共享状态前取得写锁；只重试 `SQLITE_BUSY` / `SQLITE_LOCKED` 及其扩展码。默认每次等待最多 5 秒，首次失败后最多重试 3 次，采用指数退避和少量随机延迟；事务失败先回滚并关闭连接，再重试整个数据库操作。不会重跑用户实验或文件复制。磁盘错误、约束错误和错误 SQL 直接报告。达到重试上限仍会抛错，不承诺无限并发下永不失败。

## 心跳与状态查询

进入 Run 时写入初始 `last_heartbeat_at`，随后默认每 30 秒更新；可以通过新增的可选参数调整，旧代码不需修改：

```python
from cryptolab import get_run_status, get_batch_summary, track_run

with track_run(project="示例", experiment="长实验", heartbeat_interval_s=30) as run:
    run.log("开始计算")
    # 此处执行自己的计算；后台心跳不会接触模型或用户代码。
    print(get_run_status(run.id, heartbeat_timeout_s=120))

# 也可以在另一个 Python 程序中按字符串 ID 查询：
# print(get_run_status("实际的 Run ID"))
# print(get_batch_summary("实际的 Batch ID"))
```

`get_run_status` 返回 `status`、`display_status`、开始/结束/耗时/心跳时间和进程/线程 ID。`status` 是数据库原值；只有原值为 `RUNNING` 且心跳超过阈值时，`display_status` 为 `UNCONFIRMED`（状态待确认）。查询不会写回状态，不会伪造结束时间。第一阶段没有心跳的旧记录回退到开始/创建时间判断。

`get_batch_summary` 在同一个 SQLite 读快照中返回：

```python
{
    "batch_id": "...",
    "batch_status": "COMPLETED",  # Batch 作用域状态
    "total": 5,
    "completed": 2,
    "failed": 1,
    "interrupted": 0,
    "running": 1,
    "unconfirmed": 1,
}
```

五个子状态计数互斥，相加等于 `total`；`running` 不重复包含 `unconfirmed`。两种查询的默认超时阈值均为 120 秒，可传 `heartbeat_timeout_s` 调整，建议明显大于心跳周期。

心跳在本 Run 的守护线程执行，每次新建连接，单次 busy timeout 为 250 毫秒。失败时保留上一次成功时间，向 stderr 报告；连续失败只提示一次，后续周期继续尝试，恢复后可重新变为正常运行状态。心跳故障不把正常实验自动判为失败。退出时停止并等待心跳线程结束，然后正常收尾。

强制终止后，已经提交的参数、指标、附件元数据以及已经写入的日志文件保留。数据库中的 Run 仍可能是 `RUNNING`，结束时间和耗时为空；重新打开数据库不会删除它或改判失败。`output_path` 创建的文件和日志若尚未执行退出登记，可能只有磁盘文件、没有附件元数据。SDK 不自动恢复执行实验，也不猜测其真实结果。

## 人工修订与审计

界面与 Python 接口使用同一套审计逻辑：

```python
from cryptolab import update_run

# 使用一个已经结束的 Run ID；params/metrics 为完整替换，不是增量合并。
update_run(
    "实际的 Run ID",
    {"metrics": {"weight": 25, "satisfiable": True}, "notes": "复核后校正"},
    reason="与原始结果文件核对",
)
```

允许修改 `params`、`metrics`、`notes`、`started_at`、`finished_at`、`duration_s`，以及 `solver_name`、`solver_status`、`solver_version`、`solver_runtime_s`；要求非空原因，只允许已结束的 Run。`RUNNING`（包括待确认）不允许修订，状态和 raw 快照也不能通过该接口修改。更改和 `run_changes` 逐字段审计在同一事务中提交；任一部分失败全部回滚。`actor` 固定为 `local_user`，相同内容不产生审计条目。并发修订按提交顺序串行化，后一次的 old value 来自前一次已提交值。

手工时间接受带时区的 ISO 8601 字符串并规范化为 UTC。修改开始/结束时间而不显式指定耗时时，根据两者重算耗时并追加审计；任一端未知则耗时为 `None`。JSON 参数/指标的原始快照保持冻结。已删除记录必须先恢复再编辑。界面传入 `expected_updated_at` 做乐观并发校验，防止旧页面覆盖他人的新修改；旧版不传该参数的调用仍兼容。

## 手动历史记录、软删除与恢复

```python
from cryptolab import create_manual_run, update_run, soft_delete_run, restore_run
from cryptolab.records import get_run

run_id = create_manual_run(
    project="PRESENT 差分分析", experiment="历史 MILP 实验",
    params={"rounds": 8}, metrics={"weight": 16},
    started_at="2026-10-20T09:00:00+08:00",
    finished_at="2026-10-20T09:02:00+08:00",
    solver_name="Gurobi", solver_status="OPTIMAL",
    notes="根据原始实验笔记录入", reason="整理历史实验",
)
row = get_run(run_id)
update_run(run_id, {"notes": "已核对原始结果文件"},
           reason="复核", expected_updated_at=row["updated_at"])
soft_delete_run(run_id, reason="误录，暂时隐藏")
restore_run(run_id, reason="核对后恢复")
```

`create_manual_run` 返回 UUID 字符串，也接受 `batch_id` 并复用批次参数合并规则。历史状态只能是 `COMPLETED / FAILED / INTERRUPTED`。缺失开始/结束时间、耗时保持 `None`；两端时间齐全且未提供耗时时按差值计算。`source='manual'`，不伪造进程 ID、心跳或自动运行快照。

软删除设置 `deleted_at`，默认列表与批次统计排除这些记录；不会删除磁盘文件。详情和“已删除记录”列表可查看、恢复。运行中及待确认 Run 不允许删除。手动创建、字段修订、软删除、恢复、人工结束和附件补录均记录操作原因与 UTC 审计时间；数据库变更和审计同一事务提交。

## 异常中断后的人工确认

详情页只为心跳已超时的 `RUNNING` Run 提供“人工确认异常结束”：

1. 先自行核实实验确实停止，填写原因并勾选确认。
2. 保存时在短事务内重新核对状态、原心跳和 `updated_at`；任一变化均拒绝过期操作。
3. 记录属于本机且 PID 已知时，通过信号 0 探测进程。PID 仍存活或权限不足无法确认时拒绝；不会发送终止信号。
4. 仅允许确认成 `INTERRUPTED` 或 `FAILED`；不知道实际结束时间就留空，耗时也留空。确认操作的时间单独保存在审计里。

旧记录的主机或 PID 缺失、主机名称变化时，无法自动验证，需要用户的明确确认。PID 被其他进程复用也会保守地拒绝；不要为了通过检查而修改 PID。确认会冻结自动 Run 最后已提交的参数和结果，不推断求解结论，也不恢复执行。

Python 接口同样要求读取版本和明确确认：

```python
from cryptolab import confirm_run_stopped
from cryptolab.records import get_run

row = get_run("实际的 Run ID")
# 仅在自行核实该实验已停止后调用；否则继续保持待确认。
confirm_run_stopped(
    row["id"], expected_heartbeat_at=row["last_heartbeat_at"],
    expected_updated_at=row["updated_at"], confirmed_stopped=True,
    status="INTERRUPTED", reason="核对进程已退出，实际结束时刻不明",
)
```

## 日志、附件与异常中断后的补录

日志默认显示末尾最多 256 KiB / 300 行，完整搜索流式读取，最多显示 200 段匹配内容。超长行按块搜索并保留跨块匹配；日志不会长期完整积累在 SDK 内存中。完整日志或附件在点击“准备下载”后交给 Streamlit 下载，不执行文件内容。

“检测未登记文件 → 只读扫描”只检查当前 Run 目录，显示文件名、大小和 SHA-256，不修改 Run、附件表或状态。可对运行中 Run 扫描，但只有结束或安全确认后才能补录。补录前需要核对文件已写完、填写原因并明确提交；服务器重新计算散列、检查文件身份和时间戳，变更则要求重新扫描。符号链接、硬链接、多层路径穿越、FIFO 等非普通文件和 `.copy-` 临时文件均拒绝。复制/登记失败时保留可恢复的磁盘文件，不覆盖已有文件。

后端函数也可独立于网页使用：

```python
from cryptolab.files import scan_unregistered_files, register_existing_file

scan = scan_unregistered_files("实际的 Run ID")  # 只读
# 从 scan["files"] 中选择已核实属于该实验、已写入完成的文件：
# item = ...
# register_existing_file(run_id, item["name"], expected_sha256=item["sha256"],
#                        expected_size=item["size_bytes"], reason="中断后核对补录")
```

`cryptolab.files.add_attachment(run_id, path, reason=...)` 用于给已结束记录添加附件；`open_attachment(artifact_id)` 返回只读文件上下文。`cryptolab.records` 提供 `get_run`、`list_runs`、项目/方案管理查询，所有数据库访问仍复用 `Database.read/_write`，没有额外服务或连接池。

## API 与日志边界

| 接口 | 行为 |
| --- | --- |
| `track_run(project=..., experiment=..., params=None, batch_id=None, capture_console=False, heartbeat_interval_s=30)` | 返回单次使用的上下文对象。无 Batch 时必须指定项目与方案；仅传 `batch_id` 时从数据库定位，显式项目/方案必须与 Batch 一致。 |
| `track_batch(project=..., experiment=..., name=None, common_params=None)` | 返回批次上下文，提供 `batch.id`。项目、方案按名称复用。 |
| `run.id` | UUID 字符串，结束后仍可读取。 |
| `run.log_metrics(dict)` | 立即合并保存，顶层相同 key 后写覆盖；列表和嵌套字典整体替换。 |
| `run.log_params(dict)` | 运行期间补充或替换实际参数；结束后禁止调用。 |
| `run.log_gurobi(model)` | 安全读取求解属性，保存 `metrics.gurobi` 及独立 solver 字段；再次调用替换整个 `gurobi` key。 |
| `run.log(message, level="INFO")` | 写入本 Run 的 `run.log` 并在终端显示一次。支持 DEBUG/INFO/WARNING/ERROR/CRITICAL。 |
| `run.add_file(path, name=None)` | 复制现有普通文件、立即登记附件并返回目标 `Path`；保留源文件，同名或已预留路径报错。 |
| `run.output_path(name)` | 返回安全的 `Path`，支持相对子目录；结束时仅登记实际存在的文件。重复请求同一路径返回相同位置。 |
| `get_run_status(run_id, heartbeat_timeout_s=120)` | 返回原始状态和只读推导状态 `display_status`，超时显示 `UNCONFIRMED`。超时参数仅限关键字传入。 |
| `get_batch_summary(batch_id, heartbeat_timeout_s=120)` | 返回 Batch 作用域状态和五类互斥子 Run 计数。超时参数仅限关键字传入。 |
| `update_run(run_id, changes, reason=..., expected_updated_at=None)` | 人工修订已结束记录并追加审计；`reason` 为必填关键字参数。 |

除 `.id` 外，Run 的方法只能在其 `with` 内调用。对象不能重复进入。JSON 仅接受字符串键的字典和 JSON 标量、列表、嵌套字典；不支持集合、Path、NumPy 对象、元组、循环引用及 NaN/Infinity，需由调用者明确转换。非法值会直接报错，不会转换成无意义字符串。

`capture_console=True` 将普通 Python stdout/stderr 流式写入独立文件，并保持终端可见。同一进程只要已有活跃 Run，就不能再开启捕获；捕获期间也拒绝进入另一个 Run，包含嵌套 Run。普通并行 `print()` 仍在终端显示，不承诺归属某个 Run。`run.log()` 直接使用进入时的终端，避免经由 stdout 代理再次打印。

Python 代理不保证捕获子进程、`sys.stdout.buffer`、文件描述符或 C/C++ 原生输出。Gurobi 原生日志应由用户设置 `model.Params.LogFile = str(run.output_path("gurobi.log"))`。所有交给 `output_path` 的文件必须在 Run 退出前写完并关闭；附件登记后不会持续监视文件。`run.log`、`stdout.log`、`stderr.log`、`traceback.txt` 为保留名称。拒绝绝对路径、`..`、Windows 驱动器路径及目标目录中的符号链接。`add_file` 不覆盖已有文件；`output_path` 提供路径，调用者自行决定写入方式。

普通异常标记 `FAILED`；`KeyboardInterrupt` / `SystemExit` 标记 `INTERRUPTED`，随后原异常原样向外传播。结束时冻结 `raw_params_json`、`raw_metrics_json`。记录器错误会报告到 stderr；若实验同时抛出异常，以实验异常为准。若某次记录调用失败后被用户捕获，SDK 仍会在退出时标记失败并重新抛出该记录错误，防止看似成功却丢失记录。存储完全不可写时，最终状态也可能无法更新，SDK 会明确报告；不会伪造成功。

开始/结束/创建/更新时间使用带时区的 UTC ISO 8601；`duration_s` 用单调时钟记录，包括进入时建立记录和日志的开销，但不含退出后的附件散列与收尾。`solver_runtime_s` 是求解器报告的时间。**心跳只更新 `last_heartbeat_at`，不改变 `updated_at`**。`updated_at` 表示参数、结果、状态、附件登记或人工修订等最近持久化修改；写日志不逐行更新数据库。0.2.0 已被心跳推进过的旧 `updated_at` 无法可靠还原，升级不会猜测或重写历史值。`process_id`、`thread_id` 始终表示创建 Run 的实验进程/线程。

只采集主机名、进程/线程 ID、工作目录及可识别的脚本路径。不采集环境变量、许可证、Token 或命令行参数；`command_json` 和 `git_commit` 本阶段为空。用户自己写入的日志和参数按原样保存。

## 可选 Gurobi 集成

在已有求解代码外围调用即可，SDK 不设置求解器参数、不建立模型、不调用 `optimize()`：

```python
with track_run(project="PRESENT 差分分析", experiment="MILP 最优差分路径",
               params={"rounds": 12, "time_limit": 300}) as run:
    model = build_model(12)  # 用户自己的建模函数，不属于本项目
    model.Params.TimeLimit = 300
    model.Params.LogFile = str(run.output_path("gurobi.log"))
    model.optimize()
    run.log_gurobi(model)
```

适配器读取 `Status`、`SolCount`、`Runtime`、`ObjVal`、`ObjBound`、`MIPGap`、`NodeCount`；仅在 `SolCount > 0` 时尝试读取 `ObjVal`，非 MIP 不读取 `MIPGap`。不可访问或非有限数值保存为 JSON `null`。求解器版本能获取时保存。`TIME_LIMIT`、`INTERRUPTED` 等求解状态独立于 Python Run 状态：Python 正常结束时 Run 仍为 `COMPLETED`。属性与状态语义依据 [Gurobi 模型属性文档](https://docs.gurobi.com/projects/optimizer/en/current/reference/attributes/model.html) 和 [状态码文档](https://docs.gurobi.com/projects/optimizer/en/current/reference/numericcodes/statuscodes.html)。

Gurobi Environment 不应在线程间共享；若自行并行求解，请为各线程使用独立环境，自行考虑许可证与求解器线程数。本阶段自动测试使用假模型，不代替已安装 Gurobi 环境中的真实求解验收。

## 读取与筛选 SQLite 记录

常见查询可用 `cryptolab.records.list_runs(...)`；需要自定义分析时，普通数字、字符串、布尔值也可直接使用 SQLite `json_extract` 筛选：

```python
import json
import os
import sqlite3
from pathlib import Path

home = Path(os.environ.get("CRYPTOLAB_HOME") or "~/.cryptolab").expanduser()
with sqlite3.connect(home / "cryptolab.db") as db:
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT id, status, started_at, finished_at, duration_s,
                  params_json, metrics_json
           FROM runs
           WHERE json_extract(params_json, '$.rounds') >= ?
             AND json_extract(metrics_json, '$.satisfiable') = ?
           ORDER BY started_at""",
        (8, True),
    ).fetchall()
    for row in rows:
        record = dict(row)
        record["params"] = json.loads(record.pop("params_json"))
        record["metrics"] = json.loads(record.pop("metrics_json"))
        print(record)
```

SDK 初始化六张表并建立索引，设置 `foreign_keys=ON`、WAL、5 秒 busy timeout，使用 `PRAGMA user_version=1`，重复启动不删除记录；遇到比 SDK 新的数据库版本明确报错。自己建立的 SQLite 连接如需写入，也应自行开启外键检查。附件路径相对数据根目录保存，附带大小与 SHA-256。

## 测试与本次验收

```bash
python -m pip install -e '.[web,test]'
python -m pytest -q
```

本次完整结果为 **170 passed in 5.17s**，无失败或跳过。全部测试使用临时 `CRYPTOLAB_HOME`，不写入真实默认目录。完整测试包括原有 116 项回归、时间语义、管理操作及审计回滚、安全人工确认、文件补录、筛选导出、时区和 Streamlit AppTest 交互测试。并发部分实际启动独立 Python 子进程、spawn 进程池（40/80 Run）和线程池（60/80 Run），验证日志、ID、参数及结果隔离，并检查 UI 查询与 SDK 写入同时进行。

仅安装 `[test]` 时 SDK 和管理后端测试正常执行，Streamlit 页面测试会因缺少可选依赖而跳过；完整验收需安装 `[web,test]`。具体执行环境、通过数量与实际页面验证见 [设计文档的验证记录](docs/design.md)。

## 已知限制

- 心跳是尽力而为：持有 GIL 的原生计算、系统休眠、时钟调整或长时间数据库争用可能导致延迟。待确认不等于进程死亡；心跳恢复后查询状态也会恢复。
- 支持本机文件系统，未经网络共享目录或多机环境验收。并发写入最终由 SQLite 串行化，超过有限锁等待预算会明确报错。
- 日志逐条 flush，不在内存长期积累，但不逐条 `fsync`；强制进程终止测试不等于断电持久性测试。跨进程环境使用 spawn；活跃 Run 或其他后台线程存在时不要 fork。
- Gurobi 仍为可选依赖。已验证假模型在并发 Run 中的属性与状态隔离，未进行真实许可证环境下的并发求解验证；环境、模型及求解器线程数由调用者管理。
- 界面面向个人本机数据量，查询结果和 CSV/JSON 导出暂不分页；Streamlit 下载会在内存准备文件，超大日志或附件建议从数据目录用本地工具处理。上传受 Streamlit 默认文件大小限制（200 MB/文件）。
- 附件安全读取使用 macOS/Linux 的 `dir_fd` 与 `O_NOFOLLOW`；本次在 macOS 验证，未提供 Windows 界面附件兼容性保证。磁盘文件在补录后仍可能被外部程序修改；元数据散列表示登记时的内容，不是持续文件锁。
- 没有实验调度、网页启动/终止实验、远程运行、账号、云同步或永久删除功能。
- 真正的数据库版本迁移脚本随以后 schema 变化编写；当前只提供初始版本与兼容性检查。未自动采集 Git 提交或命令行。

本项目没有修改任何已有密码分析实验。`examples/` 中均为独立模拟实验。
