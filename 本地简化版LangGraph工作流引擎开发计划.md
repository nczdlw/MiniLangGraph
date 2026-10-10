# 本地简化版 LangGraph 工作流引擎开发计划（minigraph）V4

> 状态：待审计划。本文仅定义拟议实现与验收范围；审查通过并收到明确实施指令前，不开始编码、测试、提交或推送。

## 一、摘要

在当前 `D:\MiniLangGraph` 项目中实现一个纯 Python 标准库的本地工作流引擎 `minigraph`。引擎借鉴 LangGraph 的状态图、条件路由、检查点和中断概念，不承诺 API 或边缘行为兼容。

- Python 3.10+，仅标准库，支持 Windows 和 Linux。
- 节点是同步 Python 函数；状态通过字典增量和可配置 reducer 更新。
- 执行采用超步快照与运行时同步汇聚：同一超步节点读取同一状态快照；汇聚节点等本轮所有已选中前驱到达后再运行，且每轮至多运行一次。
- 交付包含引擎包、声明式流程加载、CLI、示例、测试和最小 README。
- 自动重试、线程池并行、子图、知识库更新及 git 提交/推送不在本项目计划范围内。

## 二、V3 评估与 V4 修订

V3 已明确主要 API、JSON 流程导入、检查点生命周期和 CLI 需求，但存在以下待修正项：

1. **仓库与开发路径假设失实**：V3 将项目放在 `/workspace/minigraph`，并把当前仓库描述为知识库；实际项目目录为 `D:\MiniLangGraph`，现有内容是计划书。V4 以当前目录为项目根目录，不依赖外部知识库。
2. **汇聚分析不适合作为编译期硬限制**：V3 通过路径长度 BFS 拒绝不等长分支，在循环和条件路由下难以准确建模。V4 删除该静态拒绝，改用运行时同步汇聚与轮次级去重规则。
3. **节点失败后的批次恢复可能重复副作用**：只保存失败节点和剩余节点会丢失本超步已成功节点的写入，重跑整个批次可能重复执行成功节点。V4 检查点保存成功节点清单和未合并写入，恢复只执行失败节点及尚未执行节点。
4. **浅拷贝不足以隔离状态**：嵌套 list/dict 仍可能在同批次节点间产生污染。V4 要求节点不得原地修改输入，并为每个节点提供隔离副本；不满足状态契约时报告节点和字段路径。
5. **自定义 JSON 钩子边界不清**：`default`/`object_hook` 不自然保证任意对象可往返。V4 以 JSON 原生类型作为持久化契约；可选钩子须在写入和恢复时维持该契约。
6. **运行 ID 覆盖规则不安全**：V3 对归档和覆盖缺少显式控制。V4 禁止复用活动运行 ID；覆盖已结束运行须显式指定，并先原子归档旧运行。
7. **交付流程混入项目外工作**：V3 要求整理知识库和提交推送，超出当前引擎仓库范围。V4 仅规划本项目文件；提交、推送另行决定。

## 三、设计与接口

### 1. 公共 API

```python
graph = StateGraph(reducers: dict[str, Callable] | None = None)
graph.add_node(name: str, fn: Callable[[dict], dict])
graph.add_edge(source: str, target: str)
graph.add_conditional_edges(
    source: str,
    router: Callable[[dict], str | list[str]],
    mapping: dict[str, str] | None = None,
)
compiled = graph.compile()

compiled.invoke(
    input: dict | None = None,
    *, run_id: str | None = None,
    checkpoint_dir: str | Path = ".checkpoints",
    interrupt_before: Iterable[str] = (),
    interrupt_after: Iterable[str] = (),
    max_steps: int = 100,
    keep_last: int | None = None,
    encoder: Callable | None = None,
    decoder: Callable | None = None,
) -> dict

compiled.resume(run_id: str, *, state_update: dict | None = None, ...) -> dict
compiled.to_mermaid() -> str
```

- 入口和结束仅用 `START`、`END` 哨兵边声明；固定出边与条件出边不能同时配置。START 支持固定边或条件边，但只允许一种入口定义。
- `mapping=None` 时路由返回值是节点名；有 mapping 时对每个返回项查表。目标必须已注册；多目标路由表示扇出。
- 默认 `run_id` 使用 UUID。活动运行不可覆盖；done/failed 运行仅允许通过显式覆盖选项归档后重建。
- `state_update` 与节点输出使用相同 reducer 合并规则。
- `max_steps` 按超步计数；超限写入 failed 检查点并终止，该运行不可恢复。

### 2. 执行语义与同步汇聚

- 一个超步开始时固定状态快照和本轮候选节点；节点按稳定的注册顺序执行。每个节点接收独立的状态副本，返回值必须为状态增量字典。
- 对分支产生的下游候选节点，运行时跟踪本轮已选择的前驱到达情况。汇聚节点在所有相关前驱到达后进入后续超步，同一轮只进入一次；不等长路径自然等待较慢分支。
- 条件路由只将实际选择的目标计入本轮到达集合。循环按超步重新建立到达记录，避免前一轮到达被误认为本轮已到达。
- 同一超步内的写入在屏障处统一按节点稳定顺序经 reducer 合并，不能被同批次其他节点提前观察。
- 本期节点为同步函数、顺序执行；“扇出”表达图语义，不代表线程并发。
- `interrupt_before` 在目标节点执行前保存可恢复检查点；`interrupt_after` 在目标节点成功后保存其写入和后继，恢复不得重跑该节点。

### 3. 图编译校验

`compile()` 检查：START 入口唯一；边源和目标有效；固定边与条件边互斥；条件映射目标已注册或为 END；非 START/END 节点均可从 START 到达；至少存在一条静态可见的 END 路径。`mapping=None` 的动态路由无法完整静态验证，编译时给出提示，运行时校验目标。

编译不通过路径长度分析拒绝不等长汇聚。运行时遇到无法满足的汇聚或图无法前进时，写入包含轮次和待满足节点信息的失败检查点，并抛出可读 `GraphError`。

### 4. 状态、检查点与失败恢复

- 持久化状态限定为 JSON 原生类型：`str`、`int`、`float`、`bool`、`None`、list、dict，且字典键为字符串。节点返回值和 `state_update` 遵守相同要求。
- 可选 encoder/decoder 只用于扩展 JSON 编解码；编码后数据和解码后状态都必须符合持久化契约。序列化错误应指出节点/字段路径和值类型。
- 每个检查点记录 `schema_version`、step、状态、待运行节点、已完成节点、已收集但未合并的写入、运行状态、时间戳；失败记录节点、异常类型、消息和 traceback。meta 保存运行状态、引擎版本、图哈希和创建/更新时间。
- 写盘采用临时文件加 `os.replace`。损坏或版本不支持的检查点须返回可读错误，不得静默从头运行。恢复续写使用最大已存在 step 编号加一。
- 节点失败时保留同批次已完成节点的写入，只重跑失败节点及未执行节点；成功节点的写入在批次完整后统一合并。
- `graph_hash` 基于排序后的节点、边和静态路由映射生成。恢复时不匹配则警告并允许继续；不承诺检测函数体变化。
- `keep_last=N` 保留最近 N 个 step 检查点；默认保留全部。清理不应删除恢复所需的最新检查点或 meta。

### 5. 声明式加载、CLI 与日志

JSON 流程文件声明 imports、节点、固定边、条件边和 reducer。imports 支持 Python 模块名与 `.py` 文件路径；导入模块通过注册装饰器提供节点、路由器和 reducer。CLI 可重复传入额外模块/文件。缺少注册符号时报错并标注引用位置。

CLI 命令：`run`、`resume`、`list`、`mermaid`。退出码：成功 0；运行失败 1；中断/等待人工 2；参数错误沿用 argparse 的 2。`minigraph` logger 默认不输出；详细模式显示节点、路由和超步进度。

## 四、目录与里程碑

```text
D:\MiniLangGraph\
├── README.md
├── minigraph\
│   ├── __init__.py
│   ├── errors.py
│   ├── state.py
│   ├── graph.py
│   ├── engine.py
│   ├── checkpoint.py
│   ├── registry.py
│   ├── loader.py
│   └── cli.py
├── examples\
│   ├── 01_linear_flow.py
│   ├── 02_conditional_retry.py
│   ├── 03_approval_interrupt.py
│   └── flows\
│       └── retry_flow.json
└── tests\
    ├── test_state.py
    ├── test_graph.py
    ├── test_engine.py
    ├── test_checkpoint.py
    └── test_loader.py
```

- **M1：状态与图** — reducer、建图 API、编译校验及相应单元测试。
- **M2：执行与检查点** — 超步、同步汇聚、中断、失败保存与恢复、原子写和保留策略。
- **M3：加载器与 CLI** — 注册、JSON 流程、命令和退出码。
- **M4：示例与文档** — README、三个示例和跨平台验收。

编码保持 Python 3.10 兼容，路径使用 `pathlib`，不写死平台分隔符。

## 五、验收标准

1. 在 Python 3.10+ 上运行 `python -m unittest discover -s tests -t . -v` 全部通过。
2. 线性、条件重试、审批中断流程可运行；审批恢复后已完成节点不重跑。
3. 扇出节点观察同一超步状态；嵌套状态修改不会污染其他节点；reducer 合并顺序确定。
4. 菱形及不等长汇聚均只触发一次；条件路由、循环和动态非法目标有明确结果或可读错误。
5. 节点异常恢复不会重跑同批次已成功节点；检查点保存失败现场、待执行节点与未合并写入。
6. 损坏检查点、JSON 不兼容值、图哈希不匹配、运行 ID 冲突、`keep_last` 和 CLI 退出码均有测试覆盖。
7. CLI 可完成 run、中断、list、resume、mermaid 基本流程；在 Windows 路径下检查点读写可用。

## 六、默认假设与范围

- 项目根目录是当前 `D:\MiniLangGraph`；文件结构以第四节为准。
- 运行时同步汇聚以本轮实际选中的边为准；节点按序执行，不提供并行和外部副作用的 exactly-once 保证。
- 节点不得原地修改输入；必须返回字典形式的状态增量。
- 自动重试策略、退避、线程池、子图、知识库同步及 git 提交/推送留待另行规划。
- 此文档是待审计划；实施须由用户另行明确启动。
