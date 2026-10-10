# minigraph — 本地简化版 LangGraph 工作流引擎

纯 Python 标准库实现的本地工作流引擎，借鉴 LangGraph 的**状态图、条件路由、检查点、中断**概念。不承诺与 LangGraph 的 API 或边缘行为兼容。

- Python 3.10+，仅标准库，支持 Windows 与 Linux。
- 节点是同步 Python 函数；状态通过**字典增量 + 可配置 reducer** 更新。
- 执行采用**超步（super-step）快照 + 运行时同步汇聚**：同一超步的节点读取同一状态快照；汇聚节点等本轮所有已选中前驱到达后再运行，且每轮至多运行一次。
- 交付：引擎包、声明式流程加载、CLI、示例、测试、本文档。

> 超出范围（本期不做）：自动重试策略、线程池并行、子图、知识库同步、git 提交/推送。

---

## 安装

无需第三方依赖，只要 Python 3.10+：

```bash
# 在项目根目录（含 minigraph/ 包）下即可直接 import
python -m minigraph --help
```

推荐将项目根目录加入 `PYTHONPATH`，或在你的脚本顶部加入：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 指向本项目根
import minigraph
```

---

## 快速开始

### 1）代码建图（线性流程）

```python
from minigraph import START, END, StateGraph

def fetch(state):
    return {"raw": ["a", "b", "c"]}

def clean(state):
    return {"clean": [x.upper() for x in state["raw"]]}

g = StateGraph()
g.add_node("fetch", fetch)
g.add_node("clean", clean)
g.add_edge(START, "fetch")
g.add_edge("fetch", "clean")
g.add_edge("clean", END)

compiled = g.compile()
result = compiled.invoke({})          # -> {"raw":[...], "clean":[...]}
print(compiled.to_mermaid())          # 输出 mermaid 流程图
```

详见 [`examples/01_linear_flow.py`](examples/01_linear_flow.py)。

### 2）JSON 声明式流程（条件重试）

`examples/flows/retry_flow.json` 声明了节点、边与条件路由；被引用的符号由 `examples/flows/retry_nodes.py` 通过注册装饰器提供：

```python
from minigraph.loader import load_flow

compiled = load_flow("examples/flows/retry_flow.json")
result = compiled.invoke({"tries": 0})
```

`mapping=None` 时路由返回值即节点名；有 `mapping` 时按表查目标。多目标表示扇出。  
详见 [`examples/02_conditional_retry.py`](examples/02_conditional_retry.py)。

### 3）审批中断与恢复

```python
from minigraph import GraphInterrupt, StateGraph, START, END

compiled = g.compile()
try:
    compiled.invoke({}, interrupt_before=["approve"])
except GraphInterrupt as e:
    run_id = e.run_id          # 在 approve 前中断，等待人工

result = compiled.resume(run_id, state_update={"approved": True})  # 恢复并注入审批结果
```

恢复后已完成节点**不会重跑**。详见 [`examples/03_approval_interrupt.py`](examples/03_approval_interrupt.py)。

---

## 公共 API

| 符号                                                                                                                                  | 说明                                                                                                        |                                   |
| ----------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | --------------------------------- |
| `StateGraph(reducers=None)`                                                                                                         | 声明式建图；`reducers` 为 `{key: reducer_fn}`                                                                    |                                   |
| `.add_node(name, fn)`                                                                                                               | 注册节点，fn 签名为 `(dict) -> dict`（返回状态增量）                                                                      |                                   |
| `.add_edge(source, target)`                                                                                                         | 固定边，端点可用节点名 / `START` / `END`                                                                             |                                   |
| `.add_conditional_edges(source, router, mapping=None)`                                                                              | 条件边；\`router(state)->str                                                                                  | list[str]`；`mapping`为`{路由键: 目标}\` |
| `.compile()`                                                                                                                        | 编译并校验，返回 `CompiledGraph`                                                                                  |                                   |
| `CompiledGraph.invoke(input, *, run_id, checkpoint_dir, interrupt_before, interrupt_after, max_steps, keep_last, encoder, decoder)` | 运行，返回终态 `dict`                                                                                            |                                   |
| `CompiledGraph.resume(run_id, *, state_update, checkpoint_dir, ...)`                                                                | 从检查点续跑/恢复                                                                                                 |                                   |
| `CompiledGraph.to_mermaid()`                                                                                                        | 生成 mermaid 流程图文本                                                                                          |                                   |
| `START` / `END`                                                                                                                     | 入口 / 结束哨兵（避免与同名节点冲突）                                                                                      |                                   |
| 异常                                                                                                                                  | `GraphError` / `CompileError` / `StateContractError` / `CheckpointError` / `NodeError` / `GraphInterrupt` |                                   |

### 注册装饰器（供 JSON 流程的 imports 使用）

```python
from minigraph.registry import register_node, register_router, register_reducer

@register_node("my_node")
def my_node(state): return {"k": 1}

@register_router("my_router")
def my_router(state): return "next"          # 或 ["a", "b"] 扇出

@register_reducer("sum")
def sum(a, b): return (a or 0) + (b or 0)
```

---

## CLI

```bash
python -m minigraph run      --flow flow.json --input '{"x":1}' --checkpoint-dir .checkpoints
python -m minigraph resume   --flow flow.json --run-id <id> --state-update '{"approved":true}'
python -m minigraph list     --checkpoint-dir .checkpoints
python -m minigraph mermaid  --flow flow.json
```

常用选项（run/resume）：`--interrupt-before/--interrupt-after`（可重复）、`--max-steps`、`--keep-last`、`--run-id`、`--verbose`。

**退出码**：成功 `0`；运行失败 `1`；中断/等待人工 `2`；参数错误 `2`（argparse）。`minigraph` logger 默认静默，`--verbose` 显示节点、路由与超步进度。

---

## 设计要点

- **超步同步汇聚（token 到达模型）**：每个超步开始时固定状态快照，本轮候选节点按稳定注册顺序执行、各自拿到隔离的状态深拷贝副本，写入在屏障处按节点顺序经 reducer 合并，不能被同批次其他节点提前观察。
- **汇聚 / 不等长路径**：菱形与不等长分支在运行时等待所有相关前驱到达，每个汇聚节点每轮至多触发一次。
- **循环与重试**：回边（含自环）作为重入触发，首次触发不计入回边前驱以避免无限重入；**无条件环不会死锁**，`max_steps` 超限写入 `failed` 检查点并抛出可读 `GraphError`。
- **状态契约**：持久化状态限定为 JSON 原生类型（`str/int/float/bool/None/list/dict`，字典键必须为 `str`）。节点返回值与 `state_update` 遵守相同要求；违反时报出节点与字段路径。
- **检查点**：每步记录 `schema_version`、step、状态、待运行/已完成节点、已收集未合并写入、状态与时间戳；写盘采用临时文件 + `os.replace` 原子写。损坏或版本不支持的检查点返回可读错误，不静默从头运行。
- **失败恢复**：节点失败时保留同批次已成功节点的写入，只重跑失败节点及尚未执行节点；成功节点的写入在批次完整后统一合并。
- **图哈希与 keep_last**：`graph_hash` 由排序后的节点/边/静态路由映射生成；恢复时哈希不匹配仅**警告**并允许继续（不承诺检测函数体变化）。`keep_last=N` 保留最近 N 个 step 检查点，默认保留全部，且不会删除恢复所需的最新检查点或 meta。

---

## 验收标准对照

| # | 验收项                                                               | 对应交付 / 证据                                       |
| - | ----------------------------------------------------------------- | ----------------------------------------------- |
| 1 | `python -m unittest discover -s tests -t . -v` 全通过                | `tests/` 下 6 个测试模块                              |
| 2 | 线性 / 条件重试 / 审批中断流程可运行；审批恢复后已完成节点不重跑                               | `examples/01~03`                                |
| 3 | 扇出同快照、嵌套修改不污染、reducer 合并且顺序确定                                     | `test_engine.py`                                |
| 4 | 菱形及不等长汇聚只触发一次；条件路由 / 循环 / 动态非法目标有可读结果或错误                          | `test_engine.py`                                |
| 5 | 节点异常恢复不重跑同批次已成功节点；检查点保存失败现场 / 待执行 / 未合并写入                         | `test_engine.py` / `test_checkpoint.py`         |
| 6 | 损坏检查点 / JSON 不兼容值 / 图哈希不匹配 / 运行 ID 冲突 / `keep_last` / CLI 退出码均有测试 | `test_checkpoint.py` / `test_loader.py` / `cli` |
| 7 | CLI 可完成 run / 中断 / list / resume / mermaid；Windows 路径检查点读写可用      | `cli.py` + 跨平台冒烟（含空格路径）                         |

---

## 目录结构

```text
D:\MiniLangGraph\
├── README.md
├── minigraph\
│   ├── __init__.py        # 公共导出
│   ├── errors.py          # 异常体系
│   ├── state.py           # reducer 合并 / 状态隔离 / JSON 原生契约
│   ├── graph.py           # StateGraph API / 编译校验 / graph_hash / to_mermaid
│   ├── engine.py          # 超步 / 同步汇聚 / 条件路由 / 中断 / 失败恢复
│   ├── checkpoint.py      # 检查点读写 / 原子写 / meta / keep_last
│   ├── registry.py        # 注册装饰器
│   ├── loader.py          # JSON 流程加载
│   └── cli.py             # run/resume/list/mermaid
├── examples\
│   ├── 01_linear_flow.py
│   ├── 02_conditional_retry.py
│   ├── 03_approval_interrupt.py
│   └── flows\
│       ├── retry_flow.json
│       └── retry_nodes.py
└── tests\
    ├── test_state.py
    ├── test_graph.py
    ├── test_engine.py
    ├── test_checkpoint.py
    └── test_loader.py
```

---

## 已知限制 / 范围说明

- 节点为同步函数、顺序执行；"扇出"表达图语义，不代表线程并发。
- 运行时同步汇聚以本轮实际选中的边为准，**不提供并行和外部副作用的 exactly-once 保证**——节点不应原地修改输入，必须返回字典增量；副作用（如写文件、调用 API）的幂等由调用方负责。
- `mapping=None` 的动态路由编译期只给提示，运行时校验目标；无法静态保证 END 可达。
- 活动运行 ID 不可覆盖；`done` / `failed` 运行须显式覆盖选项归档后重建（本期未开放强制覆盖开关，冲突即报错）。
