# CryptoLab — SDK、数据库与并发可靠性

CryptoLab 是面向密码分析科研的本地实验记录 SDK，保存参数、结构化结果、时间、状态、日志和附件。它不执行或调度实验，也不包含密码算法或建模代码。Python >=3.11，核心仅使用标准库；Gurobi 和 pytest 均为可选依赖。

当前版本 0.2.0，已实现 [需求文档](docs/requirements.md) 的第一、二阶段。保留全部原有记录接口，新增心跳、状态查询、批次统计和人工修订审计。没有 Streamlit 页面或额外服务。数据库沿用第一阶段 schema v1，升级不会删除重建历史记录。原始需求文件及其副本均未改动；实现说明见 [设计文档](docs/design.md)。

## 安装

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
# 开发测试时安装：
python -m pip install -e '.[test]'
python -m pytest
```

也可以使用已有的 Conda 环境（Python >=3.11）：

```bash
conda activate YOUR_ENV
python -m pip install -e /path/to/CryptoLab
```

在每个需要调用 SDK 的 Python/Conda 环境中安装即可。各环境可通过同一个 `CRYPTOLAB_HOME` 使用同一份本机数据。核心包不需要启动服务，也不导入 `gurobipy`。已有 Gurobi 环境可以直接使用；需要安装其 Python 包时可选择 `python -m pip install -e '.[gurobi]'`，许可证另行配置。

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

## 人工修订与审计（无界面）

按照需求文档的第二阶段补齐后端审计：

```python
from cryptolab import update_run

# 使用一个已经结束的 Run ID；params/metrics 为完整替换，不是增量合并。
update_run(
    "实际的 Run ID",
    {"metrics": {"weight": 25, "satisfiable": True}, "notes": "复核后校正"},
    reason="与原始结果文件核对",
)
```

允许修改 `params`、`metrics`、`notes`、`started_at`、`finished_at`、`duration_s`；要求非空原因，只允许已结束的 Run。`RUNNING`（包括待确认）不允许修订，状态和 raw 快照也不能通过该接口修改。更改和 `run_changes` 逐字段审计在同一事务中提交；任一部分失败全部回滚。`actor` 固定为 `local_user`，相同内容不产生审计条目。并发修订按提交顺序串行化，后一次的 old value 来自前一次已提交值。

手工时间接受带时区的 ISO 8601 字符串并规范化为 UTC。修改开始/结束时间而不显式指定耗时时，根据两者重算耗时并追加审计；任一端未知则耗时为 `None`。JSON 参数/指标的原始快照保持冻结。界面编辑和手动新建历史记录仍留待第三阶段。

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
| `update_run(run_id, changes, reason=...)` | 人工修订已结束记录并追加审计；`reason` 为必填关键字参数。 |

除 `.id` 外，Run 的方法只能在其 `with` 内调用。对象不能重复进入。JSON 仅接受字符串键的字典和 JSON 标量、列表、嵌套字典；不支持集合、Path、NumPy 对象、元组、循环引用及 NaN/Infinity，需由调用者明确转换。非法值会直接报错，不会转换成无意义字符串。

`capture_console=True` 将普通 Python stdout/stderr 流式写入独立文件，并保持终端可见。同一进程只要已有活跃 Run，就不能再开启捕获；捕获期间也拒绝进入另一个 Run，包含嵌套 Run。普通并行 `print()` 仍在终端显示，不承诺归属某个 Run。`run.log()` 直接使用进入时的终端，避免经由 stdout 代理再次打印。

Python 代理不保证捕获子进程、`sys.stdout.buffer`、文件描述符或 C/C++ 原生输出。Gurobi 原生日志应由用户设置 `model.Params.LogFile = str(run.output_path("gurobi.log"))`。所有交给 `output_path` 的文件必须在 Run 退出前写完并关闭；附件登记后不会持续监视文件。`run.log`、`stdout.log`、`stderr.log`、`traceback.txt` 为保留名称。拒绝绝对路径、`..`、Windows 驱动器路径及目标目录中的符号链接。`add_file` 不覆盖已有文件；`output_path` 提供路径，调用者自行决定写入方式。

普通异常标记 `FAILED`；`KeyboardInterrupt` / `SystemExit` 标记 `INTERRUPTED`，随后原异常原样向外传播。结束时冻结 `raw_params_json`、`raw_metrics_json`。记录器错误会报告到 stderr；若实验同时抛出异常，以实验异常为准。若某次记录调用失败后被用户捕获，SDK 仍会在退出时标记失败并重新抛出该记录错误，防止看似成功却丢失记录。存储完全不可写时，最终状态也可能无法更新，SDK 会明确报告；不会伪造成功。

开始/结束/创建/更新时间使用带时区的 UTC ISO 8601；`duration_s` 用单调时钟记录，包括进入时建立记录和日志的开销，但不含退出后的附件散列与收尾。`solver_runtime_s` 是求解器报告的时间。心跳更新 `last_heartbeat_at` 和 `updated_at`；活跃状态判断只使用心跳，因为其他修改也会更新 `updated_at`。`process_id`、`thread_id` 始终表示创建 Run 的实验进程/线程。

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

无需额外查询层，普通数字、字符串、布尔值可使用 SQLite `json_extract` 筛选：

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

运行 `python -m pytest -q`。全部测试自动使用临时 `CRYPTOLAB_HOME`，不写入真实默认数据目录。

本次完整结果为 **116 passed**：保留 70 项第一阶段测试（初始心跳断言按新行为调整），新增 46 项。覆盖 4 个独立 Python 进程同时初始化、进程池 40/80 Run、线程池 60/80 Run、并行查询、ID/参数/结果/日志/附件隔离、子进程失败与强制终止、锁竞争与有限重试、周期心跳及其停止、修订审计与事务回滚。

两个并行示例分别实际生成 40 和 60 个完成 Run；默认 30 秒心跳也做了真实等待验证。第一阶段真实数据库副本追加新记录后，旧记录保持不变。执行环境与详细结果见 [设计文档的验证记录](docs/design.md)。

## 已知限制与后续阶段

- 心跳是尽力而为：持有 GIL 的原生计算、系统休眠、时钟调整或长时间数据库争用可能导致延迟。待确认不等于进程死亡；心跳恢复后查询状态也会恢复。
- 支持本机文件系统，未经网络共享目录或多机环境验收。并发写入最终由 SQLite 串行化，超过有限锁等待预算会明确报错。
- 日志逐条 flush，不在内存长期积累，但不逐条 `fsync`；强制进程终止测试不等于断电持久性测试。跨进程环境使用 spawn；活跃 Run 或其他后台线程存在时不要 fork。
- Gurobi 仍为可选依赖。已验证假模型在并发 Run 中的属性与状态隔离，未进行真实许可证环境下的并发求解验证；环境、模型及求解器线程数由调用者管理。
- 第三阶段尚未实现：全部 Streamlit 页面、手动补录、界面编辑、软删除、日志/附件浏览、筛选和导出。没有 `web` 依赖或 Web 启动入口。
- 真正的数据库版本迁移脚本随以后 schema 变化编写；当前只提供初始版本与兼容性检查。未自动采集 Git 提交或命令行。

本项目没有修改任何已有密码分析实验。`examples/` 中均为独立模拟实验。
