# CryptoLab v1.0 开发需求说明

> 状态：第一版范围确认稿（用于交给 Codex 分阶段实现）  
> 平台：macOS（Apple Silicon 优先），兼容常见 Python/Conda 环境  
> 定位：面向密码分析科研的轻量级、本地优先实验记录与管理工具

## 1. 项目目标

开发一个可长期使用的本地工具，记录密码分析实验的**参数、结构化结果、原始输出、实验附件、运行时间和复现信息**。用户既可以通过少量 Python 语句自动保存，也可以在 Web 页面手动新增和修改历史记录。

典型任务包括 PRESENT / SKINNY / AES 等算法的差分或线性分析、Gurobi MILP 求解、SAT/PySAT 搜索及普通 Python 实验。**工具只记录和管理实验，不负责实现密码算法、不负责执行或调度实验。**

核心原则：

1. 对原有科研代码侵入最小。
2. 同一组参数重复执行必须产生新记录，不覆盖历史结果。
3. 同时支持独立终端、多进程、多线程中的实验记录。
4. 原始数据、人工修订和日志要有清晰边界，不能静默丢失。
5. 本地离线使用，依赖简单，便于个人维护。

## 2. 技术选择与运行边界

- Python **>=3.11**；推荐标准库优先，使用 `pyproject.toml` 管理安装。
- SQLite 存结构化数据；启用 WAL、外键和 busy timeout，写操作只占用短事务。
- Streamlit 构建管理界面；仅绑定 `127.0.0.1`，不做云端服务。
- pytest 负责自动化测试。
- Gurobi 为**可选集成**：核心包不能在导入时强制要求安装 `gurobipy`。
- 使用 `~/.cryptolab/` 作为默认数据根目录，允许环境变量 `CRYPTOLAB_HOME` 覆盖；项目源码与实验数据分离。
- 同一个本机数据目录可被多个 Conda 环境中的程序访问。**不要**把 WAL 数据库放在网络共享文件系统中。
- 只支持单机个人使用；第一版不加入账号、云同步或远程数据库。

## 3. 概念与数据关系

### 3.1 层级

- **Project（项目）**：一个研究主题，如“PRESENT 差分分析”。
- **Experiment（实验方案）**：一个具体方法或模型，如“MILP 最优差分路径”。
- **Batch（实验批次，可选）**：一次批量运行计划，如“8 至 20 轮搜索”。
- **Run（运行记录）**：某一次实际执行（或一次手工补录）的参数、结果和附件。
- **Artifact（附件）**：某次 Run 的日志、结果文件、LP/MPS、CSV 等。

关系：`Project 1:N Experiment`；`Experiment 1:N Batch`；`Experiment 1:N Run`；`Batch 1:N Run`（Run 的 `batch_id` 可以为空）。

### 3.2 约束

- **每一次执行都创建新的 `run_id`**；参数相同也不合并。
- Batch 不是 Run；它只提供共同参数、分组和汇总。
- 批次公共参数和单次参数在创建 Run 时合并，**单次参数优先**；在 Run 中保存合并后的完整有效参数快照，避免以后编辑 Batch 改变历史事实。
- 自动生成的 Run 和手动新建的 Run 使用同一数据结构，用 `source=auto/manual` 区分。
- Gurobi 求解状态与 Python 运行状态分别保存；例如程序正常结束且 Gurobi 超时，Run 状态为 `COMPLETED`，求解状态为 `TIME_LIMIT`，不应判为 Python 失败。

## 4. 第一版 Python API（应严格按此接口交付）

### 4.1 单次实验

```python
from cryptolab import track_run

with track_run(
    project="PRESENT 差分分析",
    experiment="MILP 最优差分路径",
    params={"rounds": 12, "time_limit": 300},
    capture_console=True,  # 仅在单个 Python 进程中没有并发 Run 的场景使用
) as run:
    model = build_model(12)  # 用户已有代码，不属于 CryptoLab
    model.Params.TimeLimit = 300
    model.Params.LogFile = str(run.output_path("gurobi.log"))
    model.optimize()
    run.log_gurobi(model)
    run.log_metrics({"extra_metric": 42})
    run.add_file("result.txt")  # 若文件存在，可复制并登记
```

`track_run(...)` 是上下文管理器：进入时立刻创建 Run、记录开始时间；退出时记录结束时间、耗时、状态，关闭相关日志资源。抛异常时尽量记录异常类型和 traceback，并**原样重新抛出异常**。不得因记录器错误隐去用户代码的原始异常。

### 4.2 批量并行实验

```python
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from cryptolab import track_batch, track_run


def worker(rounds, batch_id):
    with track_run(batch_id=batch_id, params={"rounds": rounds}) as run:
        model = build_model(rounds)
        model.optimize()
        run.log_gurobi(model)


if __name__ == "__main__":
    with track_batch(
        project="PRESENT 差分分析",
        experiment="MILP 最优差分路径",
        name="8-12 轮测试",
        common_params={"time_limit": 300},
    ) as batch:
        with ProcessPoolExecutor(max_workers=3) as pool:
            list(pool.map(worker, range(8, 13), repeat(batch.id)))
```

- `track_batch(...)` 返回带 `.id` 的上下文对象。
- 每个进程/线程各自调用 `track_run(...)`；传入简单的 `batch_id`，**不跨进程传递** Run 对象、SQLite 连接或 Gurobi 模型。
- 允许 `track_run(project=..., experiment=...)` 不指定 Batch；也允许仅传 `batch_id`，通过数据库定位项目和实验方案。
- 初版不必为多线程自动路由 `print()`；多线程请使用 `run.log(...)`。

### 4.3 Run 最小方法集

| API | 行为 |
|---|---|
| `run.id` | 获取唯一运行 ID（建议 UUID4） |
| `run.log_metrics(dict)` | 增量保存结构化结果；支持 JSON 可序列化的标量、列表、字典；相同 key 后写覆盖且需有清晰说明 |
| `run.log_params(dict)` | 在实验进行中补充实际使用的参数；结束后的自动原始快照冻结 |
| `run.log_gurobi(model)` | 从 Gurobi Model 安全提取可访问的求解属性；不能强制依赖 Gurobi |
| `run.log(message, level="INFO")` | 写入当前 Run 专属日志，并可同步显示终端；线程间不串日志 |
| `run.add_file(path, name=None)` | 将已存在文件复制进本 Run 目录，并登记 Artifact；不得删除源文件 |
| `run.output_path(name)` | 返回 Run 目录中的安全路径，供实验直接写入；Run 结束时登记实际存在的文件 |
| `batch.id` | 获取 Batch ID |

不可序列化的数据须明确报错或报告记录失败，不允许静默写成无意义字符串。`params` 与 `metrics` 的查询、筛选至少支持常见数字、字符串和布尔值。

### 4.4 终端与日志行为

- 非并发单 Run 进程允许 `capture_console=True`，将常见 Python `print()`/`stderr` **流式**保存为 `stdout.log`、`stderr.log`，同时保持原终端可见；不能等程序结束再统一写入。
- 若同一进程存在并发的多个 Run，不允许各自修改全局 `sys.stdout` 争夺输出。应拒绝该模式并给出明确提示，要求使用 `run.log()`；普通 `print()` 仍能在终端显示，但不能承诺逐线程归档。
- `run.log()` 为每条 Run 使用专用 logger/handler 与文件；不复用其他 Run 的日志文件，也不重复打印。
- Gurobi 日志优先通过 Gurobi 自身 `LogFile` 写入 `run.output_path("gurobi.log")`；不要依赖 `print()` 捕获 C/C++ 求解器内部输出。
- 子进程 stdout 或底层 native 输出未必能被 Python stdout 代理捕获，文档必须说明限制。
- 自动记录不能强制改变 Gurobi 参数、建立模型的逻辑或用户的求解流程。

### 4.5 Gurobi 适配规则

- 按能力探测并保护性读取：`Status`、`SolCount`、`Runtime`、`ObjVal`、`ObjBound`、`MIPGap`、`NodeCount` 等。
- 仅在有有效可行解、相关属性可用时读取解的目标值；对于不适用的字段保存 `null`，不要制造默认的 `0`。
- `TIME_LIMIT` / `INTERRUPTED` / `INFEASIBLE` / `OPTIMAL` 等保留真实求解状态；对非 MIP 模型不要强求 `MIPGap`。
- 保存求解器版本（可获取时）及 `solver_runtime_s`，并与整个实验的 `duration_s` 区分。
- Gurobi Environment 不线程安全；SDK 不负责在用户线程之间共享环境或模型，示例应提醒为各线程使用独立环境。

## 5. SQLite 数据模型

建议六张核心表，字段可补充但不得随意更改主要语义：

### 5.1 `projects`

- `id TEXT PRIMARY KEY`
- `name TEXT NOT NULL UNIQUE`
- `description TEXT`
- `created_at TEXT NOT NULL`

### 5.2 `experiments`

- `id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL REFERENCES projects(id)`
- `name TEXT NOT NULL`
- `method TEXT`（如 MILP/SAT/Other，可为空）
- `cipher_name TEXT`（如 PRESENT/SKINNY/AES，可为空，用于算法筛选）
- `description TEXT`
- `created_at TEXT NOT NULL`
- 约束 `UNIQUE(project_id, name)`

### 5.3 `batches`

- `id TEXT PRIMARY KEY`
- `experiment_id TEXT NOT NULL REFERENCES experiments(id)`
- `name TEXT`
- `common_params_json TEXT NOT NULL DEFAULT '{}'`
- `status TEXT NOT NULL`
- `started_at TEXT`、`finished_at TEXT`
- `created_at TEXT NOT NULL`、`updated_at TEXT NOT NULL`

### 5.4 `runs`（最重要）

- 标识：`id TEXT PRIMARY KEY`、`experiment_id TEXT NOT NULL`、`batch_id TEXT NULL`
- 内容：`params_json TEXT NOT NULL`、`metrics_json TEXT NOT NULL`、`notes TEXT`、`source TEXT NOT NULL`（auto/manual）
- 原始快照：`raw_params_json TEXT`、`raw_metrics_json TEXT`（自动运行结束时冻结，用于保留未经人工修订的结果）
- 双重状态：`status TEXT NOT NULL`（RUNNING/COMPLETED/FAILED/INTERRUPTED）；`solver_name TEXT`、`solver_version TEXT`、`solver_status TEXT`
- 时间：`started_at TEXT`、`finished_at TEXT`、`duration_s REAL`、`solver_runtime_s REAL`、`created_at TEXT NOT NULL`、`updated_at TEXT NOT NULL`、`last_heartbeat_at TEXT`
- 并发：`hostname TEXT`、`process_id INTEGER`、`thread_id INTEGER`
- 复现：`script_path TEXT`、`git_commit TEXT`、`command_json TEXT`、`working_directory TEXT`
- 异常：`error_type TEXT`、`error_message TEXT`、`traceback_path TEXT`
- 软删除：`deleted_at TEXT NULL`
- 外键正确引用 Experiment、Batch；插入 Run 时校验 Batch 与 Experiment 属于同一方案。

### 5.5 `artifacts`

- `id TEXT PRIMARY KEY`
- `run_id TEXT NOT NULL REFERENCES runs(id)`
- `kind TEXT`（stdout/stderr/gurobi/result/model/other）
- `original_name TEXT`、`relative_path TEXT NOT NULL`、`size_bytes INTEGER`、`sha256 TEXT`、`created_at TEXT`
- 只存相对于数据根目录的安全路径，便于搬迁；不要将用户输入直接拼成可穿越目录的路径。

### 5.6 `run_changes`

- `id TEXT PRIMARY KEY`
- `run_id TEXT NOT NULL REFERENCES runs(id)`
- `changed_at TEXT NOT NULL`
- `field_name TEXT NOT NULL`
- `old_value_json TEXT`、`new_value_json TEXT`
- `reason TEXT`、`actor TEXT`（目前固定 local_user）
- 人工更改需在事务中同时修改 `runs` 并追加审计记录。原始快照不可被界面修改。

### 5.7 索引与升级

- 为 `runs(experiment_id, started_at)`、`runs(batch_id, started_at)`、`runs(status, started_at)` 建索引；为 `artifacts(run_id)`、`run_changes(run_id)` 建索引。
- JSON 参数的高级筛选优先采用 `json_extract`；初版无需实现任意 JSON 查询语言。
- 为后续数据库升级设计 `schema_version`（可通过 `PRAGMA user_version`），不能每次启动删除重建旧表。
- 所有 SQL 使用参数化查询，避免拼接用户输入。

## 6. 时间与运行状态的精确定义

- `started_at`：真实实验开始时间；进入 `track_run()` 时自动填。
- `finished_at`：实验结束时间；退出时填。
- `duration_s`：实验总实际耗时；自动运行时以 `time.perf_counter()` 等单调时钟计算。
- `solver_runtime_s`：求解器独立报告的计算时间，可能小于总耗时。
- `created_at`：数据库记录创建时间。
- `updated_at`：记录最近一次修改时间。
- `last_heartbeat_at`：最近一次成功报告仍在运行的时间。
- 所有时间在数据库用**带时区的 UTC ISO 8601** 保存；界面转换为本地时区显示，时间筛选可按年月日和区间进行。
- 手动添加历史记录允许填写开始和结束日期时间；如仅知道实验日期，允许只填开始日期/时间或备注，不伪造结束时间和耗时。手动修订时间要进入 `run_changes`。
- 运行中超过心跳阈值仅在界面显示“状态待确认”，不要自动判断为失败。`kill -9`、断电等情形下 `finished_at` 可能为空。
- Python 代码正常退出但求解器 `TIME_LIMIT` 仍然属于 Run `COMPLETED`。
- Batch 状态可根据作用域退出和子 Run 汇总呈现，不覆盖子 Run 的独立状态。

## 7. 并发可靠性

1. 每个 Run 使用 UUID 和独立目录；不会因项目名、轮数相同发生路径冲突。
2. 每个 Python 进程/线程按需创建自己的 SQLite 连接；连接绝不跨进程传递，避免跨线程共享。
3. 开启 WAL、`busy_timeout`（如 5 秒）、`foreign_keys=ON`；数据库锁竞争可执行有限次数带退避的重试，但不得无限循环。
4. 写事务仅包含创建记录、更新状态、写指标/附件元数据、审计记录等短操作；**不能在 `model.optimize()` 运行期间持有写事务**。
5. 每条 Run 具有专属 logger 与附件目录；多线程必须显式传递 `run`，不要依赖跨线程自动继承隐式上下文。
6. 运行中低频更新心跳（建议 30 秒左右，具体可配置），不得为日志每一行开启数据库事务。心跳是尽力而为；如果长期 native 计算阻塞 Python，界面可能出现“待确认”。
7. 记录器故障不得静默伪造成功；应向 stderr 报告明确错误，并在异常处理时尽量不掩盖原实验异常。
8. 支持不同终端、多个 Conda 环境共同写入同一**本机**数据目录。

## 8. 附件与日志存储

建议结构：

```text
~/.cryptolab/
├── cryptolab.db
├── runs/
│   ├── <run-uuid-1>/
│   │   ├── stdout.log
│   │   ├── stderr.log
│   │   ├── run.log
│   │   ├── gurobi.log
│   │   └── result.txt
│   └── <run-uuid-2>/
│       ├── run.log
│       └── path.json
└── backups/
```

- `run.add_file()` 默认复制，不删除源文件；同名附件不能静默覆盖，必须报错或生成无冲突名称。
- `run.output_path()` 不允许绝对路径、`..` 等路径穿越；写入文件后在 Run 结束时登记实际存在的附件。
- 复制时可先写临时文件再原子重命名，确保中途失败不会生成貌似完整的附件。
- 附件在数据库中登记大小和相对路径；SHA-256 可选但建议实现。
- 日志实时追加，避免将整份日志一次性加载到内存；界面中大日志默认展示尾部或有限行，支持搜索和下载。
- 所有源码路径、日志都可能包含敏感数据，不自动收集环境变量、许可证密钥或 Token；命令行参数可能包含密钥，采集时须支持脱敏或不自动采集；界面不允许执行附件内容。
- 手动删除记录默认采用软删除，不直接清理附件，避免误删后无法恢复。

## 9. Streamlit 本地界面

第一版可采用四个页面或相近布局：

### 9.1 项目与实验方案

- 新建、重命名、查看项目与实验方案；允许描述及方法标签。
- 展示某实验方案下的单次运行与 Batch。

### 9.2 运行列表

- 展示 Run ID（短显示）、项目、方案、批次、轮数（如果有）、来源、开始时间、耗时、运行状态、求解状态。
- 支持关键词、项目、算法/实验、Batch、运行状态、日期范围筛选；时间排序。
- 提供“运行中 / 历史记录”筛选；运行中视图允许定期刷新。
- 提供单条详情入口、CSV/JSON 导出。

### 9.3 运行详情

四个区块：

1. **参数**：固定字段与 JSON 参数，允许人工编辑。
2. **结果**：求解状态、目标值、用时、其他 JSON 指标，允许人工修订。
3. **日志**：查看 `stdout.log`、`stderr.log`、`run.log`、`gurobi.log` 等。
4. **附件**：上传、登记、查看元信息、下载已有文件。

同时显示运行时间、创建/修改时间、复现信息、修改历史。人工编辑需有“保存”动作，并将变更写入审计表；不得静默修改自动原始快照。

### 9.4 手动添加

- 选择项目、实验方案，Batch 可选。
- 输入参数和结果，推荐采用键值字段编辑器或有效 JSON 文本框，验证数据类型。
- 可填写真实实验日期时间、状态、备注，上传已有结果文件与日志。
- 与自动 Run 使用同样的数据表。

UI 使用 Streamlit 的原生组件；**不追求复杂前端样式**。界面只读或写数据库，不应成为自动记录时必须运行的服务。

## 10. 项目目录建议

```text
CryptoLab/
├── pyproject.toml
├── README.md
├── src/cryptolab/
│   ├── __init__.py
│   ├── api.py
│   ├── database.py
│   ├── models.py
│   ├── recorder.py
│   ├── logging_utils.py
│   ├── artifacts.py
│   └── adapters/
│       └── gurobi.py
├── web/
│   ├── app.py
│   └── pages/
├── examples/
│   ├── single_run.py
│   ├── batch_run.py
│   ├── multiprocess_run.py
│   └── multithread_run.py
├── tests/
└── docs/
    └── design.md
```

`pip install -e .` 可安装轻量 SDK；`pip install -e ".[web]"` 安装可选 Web 依赖。核心 SDK 不需要用户先运行 Streamlit 服务。提供最简单的启动方式（例如 `python -m streamlit run web/app.py`）；文档写明 Conda 环境中的安装步骤。

## 11. 开发阶段与验收标准

### 阶段 1：SDK 与数据库（第一轮 Codex 任务，只做这一阶段）

**实现**：包安装、数据库初始化与迁移基础、项目/实验/Batch/Run 关系、单次与批量 API、时间记录、JSON 结果、异常状态、附件保存、可选 Gurobi 适配、基础单进程日志。

**验收**：

- `pip install -e .` 成功；不装 `gurobipy` 也能 `import cryptolab`。
- 单次实验能够产生完整 Run，且开始、结束、耗时和状态准确。
- 相同参数重复运行两次生成两个不同 Run ID。
- 批量 5 轮生成 1 Batch + 5 Run，完整参数包含公共参数和每轮参数。
- 普通 Python / SAT 风格的自定义 metrics 正常保存并可通过 SQLite 读回。
- 正常的 `print()` 可在明确开启捕获的单 Run 场景下记录；`run.log()` 写入独立日志。
- 发生异常后 Run 标记 FAILED，traceback 可查看，原异常仍会向调用者抛出。
- 所有测试使用临时数据库目录，不写入真实 `~/.cryptolab/`。

### 阶段 2：并发与可靠性

**实现**：独立连接、WAL/重试、心跳、并行日志隔离、状态待确认、数据变更审计及必要的恢复逻辑。

**验收**：

- 至少 4 个进程各创建 20 条 Run；完成后数量准确、ID 唯一，无混淆、无持久锁错误。
- 至少 4 个线程各创建 20 条 Run；使用 `run.log()` 时内容只进入各自日志文件。
- 同时存在 Streamlit 数据查询和 SDK 写入，不发生数据损坏。
- 某一 Run 报错，不影响其他 Run 的已保存记录。
- 模拟突然终止，Run 的已写入数据保留，心跳超时只标记“状态待确认”。
- 手动修改参数、指标、时间均产生正确 `run_changes`，原始快照不变。

### 阶段 3：Streamlit 管理界面

**实现**：项目页、实验列表、运行详情、手动添加/修改、日志/附件、筛选、CSV/JSON 导出、批次基础汇总。

**验收**：

- 不打开网页也可以独立运行 Python 实验并保存记录。
- 网页能看到刚完成的实验和仍在运行的实验；显示本地时间。
- 支持手动添加历史记录，正确编辑原有记录，并查看修改历史。
- 日志和附件可以预览/下载；软删除可防误删。
- 能筛选不同轮数、日期和求解状态，正确导出结果。

### Gurobi 集成补充验证

- 在用户已安装 Gurobi 的环境中验证 `OPTIMAL`、`TIME_LIMIT`（有解/无解）等状态；不可用的指标为 `null`。
- 不允许为了方便测试而强制所有环境安装 Gurobi；自动化单元测试可使用 mock/fake Model。
- 例子中提醒：并行 Gurobi 线程不能随意共享一个 Environment；许可证和求解器自身线程数限制由用户决定。

## 12. 第一版明确不做

- 不集成密码算法实现，也不编写 MILP/SAT 建模功能。
- 不做云同步、账号权限、多用户共享。
- 不做从 Web 页面直接启动/停止 Gurobi 作业或调度计算。
- 不做复杂数据可视化、科研论文自动生成。
- 不自动解析任意终端文本为结构化指标；`log_metrics()` 是结构化数据的可靠来源。
- 不承诺在并行多线程中自动捕获并按 Run 精确分流所有 `print()`、子进程或 native 层输出。
- 不引入 Redis、Celery、Docker、FastAPI 或重量级 ORM，除非经讨论证明必要。

## 13. Codex 执行约束

1. **严格分阶段实施**，初次只执行阶段 1，不要提前开发 Streamlit 页面。
2. 在开发前列出简洁的实现计划、拟创建的文件和接口，之后直接执行，不要扩大范围。
3. Python 代码优先易读、少依赖、带必要类型提示和简短注释，避免过度设计。
4. 数据库操作集中封装；避免所有业务逻辑塞入一个 `app.py`。
5. 编写 `README.md`，给出安装、单次、批量、多进程和多线程示例，以及 API 适用边界。
6. 每阶段完成后运行 pytest，报告通过数量、失败及原因；展示真实的 SQLite 记录示例。
7. 不修改用户已有的密码分析项目；使用 `examples/` 中独立的模拟实验验证。
8. 如果某项设计存在冲突，优先保证数据正确性、时间记录和日志隔离，再保证调用简单；在完成报告中写清取舍。

## 14. 参考技术资料

- Python Logging Cookbook：https://docs.python.org/3/howto/logging-cookbook.html
- Python contextvars：https://docs.python.org/3/library/contextvars.html
- SQLite WAL：https://www.sqlite.org/wal.html
- Gurobi Python Environment：https://docs.gurobi.com/projects/optimizer/en/current/reference/python/env.html
- Gurobi Python Model：https://docs.gurobi.com/projects/optimizer/en/current/reference/python/model.html
- Streamlit data editor：https://docs.streamlit.io/develop/api-reference/data/st.data_editor
