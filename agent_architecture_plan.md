# 设备检修系统 Agent 架构设计方案

## 1. 项目定位

本项目面向设备检修场景，核心目标不是做一个普通聊天问答系统，而是构建一个可以支持“现场检修决策与作业闭环”的 Agent Harness 系统。

系统需要完成：

- 用户输入故障现象、设备名称或检修任务；
- Agent 生成可追踪的检修计划；
- 调用手册检索、AI Coding、合规检查等工具；
- 返回诊断建议、SOP、证据、工具调用记录、评估结果；
- 通过 Trace 和 Memory 支持演示、调试和多轮上下文；
- 后续接入真实 PDF 解析、LlamaIndex 检索、多模态输入和知识图谱。

当前阶段的核心策略是：

> 先完成 LangGraph Harness + FastAPI + React 的前后端闭环，知识库先使用 placeholder evidence，等 PDF 解析和真实索引完成后替换 Retriever 实现，不改前后端协议。

---

## 2. 当前实现快照

截至当前版本，项目已经完成以下 MVP 能力：

| 模块 | 当前状态 |
|---|---|
| FastAPI `/api/query` | 已完成 MVP，返回统一 envelope |
| React + Vite 前端 | 已真实调用 `/api/query` |
| Agent 编排底层 | 已迁移为 LangGraph `StateGraph(HarnessState)` |
| LLM 层 | 已预留 DeepSeek V4 client，支持 fallback |
| LangChain | 用于 Prompt / LLM client 封装 |
| LlamaIndex | 当前为兼容 placeholder，真实索引待接入 |
| Retriever | 返回符合最终 schema 的 placeholder evidence |
| ToolRegistry | 已有 `manual_lookup`、`ai_coding`、`compliance_check` |
| AI Coding | 已接入工具分支 |
| Sandbox | 只允许受限 Python / 只读 SQL，Shell 禁止执行 |
| TraceStore | 已记录计划、证据、回答、评估、sandbox result |
| MemoryStore | 已支持 session 级摘要 memory |
| 前端 Artifact | 已展示 answer、SOP、evidence、tool_calls、evaluation、trace_id、ai_coding |
| PDF 解析 | 下一阶段 |
| 真实 LlamaIndex 索引 | 下一阶段 |
| 多模态检索 | 远期规划 |
| 知识图谱 | 远期规划 |
| Human Approval | 远期规划 |

---

## 3. 总体架构

```text
用户
 |
 | 故障问题 / 检修任务 / 设备名称 / 后续图片输入
 v
React + Vite 前端
 |
 | POST /api/query
 v
FastAPI API 层
 |
 | QueryRequest
 v
AgentHarness 外观层
 |
 | graph.ainvoke(initial_state, thread_id=session_id)
 v
LangGraph StateGraph
 |
 +-- intake_node          输入归一化、session_id、trace_id
 +-- memory_load_node     读取 session 历史摘要
 +-- plan_node            生成可展示计划
 +-- retrieval_node       调用 manual_lookup 工具
 +-- route_ai_coding      判断是否进入 AI Coding 分支
 +-- ai_coding_node       生成 Python / SQL 脚本
 +-- sandbox_node         执行受限 Python / 只读 SQL
 +-- draft_answer_node    调用 DeepSeek V4 或 fallback 生成回答
 +-- compliance_node      调用合规检查工具
 +-- evaluator_node       生成安全/合规/置信度评估
 +-- trace_node           写入 TraceStore
 +-- memory_save_node     写入 MemoryStore 摘要
 +-- finalize_node        构造 QueryResponse
 |
 v
统一 ApiResponse envelope
 |
 v
前端三栏工作台展示
 |
 +-- 中间对话区：answer
 +-- SOP tab：sop + confidence + issues
 +-- 证据 tab：evidence
 +-- 记录 tab：trace_id + tool_calls + ai_coding + sandbox_result


 4. 技术栈分工

本项目底层采用：

LangGraph + LangChain + DeepSeek V4 + LlamaIndex + FastAPI + React

各层职责如下：

层	技术	职责
API 层	FastAPI	提供 /api/query、统一响应 envelope、异常处理
Agent 编排层	LangGraph	管理状态、节点、条件分支、checkpoint
LLM 调用层	LangChain + DeepSeek V4	Prompt、LLM 调用、fallback、结构化输出
知识库检索层	LlamaIndex	后续负责 PDF chunk 索引和 evidence 检索
工具层	ToolRegistry	统一注册和执行工具
安全执行层	SandboxExecutor	受限 Python / SQL 执行，禁止 Shell
评估层	Evaluator	检查安全、合规、证据充分性、置信度
Trace 层	TraceStore	记录执行过程，供前端和演示使用
Memory 层	MemoryStore + LangGraph thread_id	保存 session 摘要和图状态
前端	React + Vite	展示对话、SOP、证据、Trace、AI Coding
5. Agent Harness 工作流

当前运行 Harness 采用固定图流程：

START
  |
  v
intake_node
  |
  v
memory_load_node
  |
  v
plan_node
  |
  v
retrieval_node
  |
  v
route_ai_coding
  |
  +-- needs_ai_coding = false --> draft_answer_node
  |
  +-- needs_ai_coding = true
          |
          v
      ai_coding_node
          |
          v
      sandbox_node
          |
          v
      draft_answer_node
  |
  v
compliance_node
  |
  v
evaluator_node
  |
  v
trace_node
  |
  v
memory_save_node
  |
  v
finalize_node
  |
  v
END
当前节点职责
节点	职责
intake_node	保留 question/device/session_id，初始化 trace、warnings、tool_calls 等字段
memory_load_node	按 session_id 读取历史摘要
plan_node	生成当前检修任务计划
retrieval_node	通过 manual_lookup 获取手册 evidence
route_ai_coding	判断是否需要 AI Coding
ai_coding_node	生成脚本结构 {language, script, explanation, warnings}
sandbox_node	对 Python / SQL 做受限执行，Shell 一律拒绝
draft_answer_node	使用 prompt + context 调用 DeepSeek V4，失败时 fallback
compliance_node	调用 compliance_check 工具
evaluator_node	生成 EvaluationResult
trace_node	写入完整执行记录
memory_save_node	写入会话摘要，不保存完整 trace
finalize_node	构造前端需要的 QueryResponse


6. HarnessState 设计

LangGraph 使用 HarnessState 作为状态 schema，节点返回局部 state update，由 schema 进行字段合并。

推荐状态结构：

{
  "question": "发动机无法启动怎么办",
  "device_name": "摩托车发动机",
  "device_model": null,
  "session_id": "demo-session",
  "trace_id": "uuid",
  "memory": [],
  "plan": [
    {
      "step": "调用 manual_lookup 检索维修手册证据",
      "status": "已完成"
    }
  ],
  "evidence": [
    {
      "source": "manual::摩托车发动机",
      "page": null,
      "snippet": "当前为 LlamaIndex 兼容占位证据；后续将替换为真实的手册分块。",
      "score": 0.42,
      "metadata": {
        "retriever": "llama-index-placeholder",
        "question": "发动机无法启动怎么办"
      }
    }
  ],
  "tool_calls": [
    {
      "tool_name": "manual_lookup",
      "input": {
        "question": "发动机无法启动怎么办"
      },
      "output": [],
      "status": "success",
      "duration_ms": 0
    }
  ],
  "needs_ai_coding": false,
  "ai_coding": null,
  "sandbox_result": null,
  "answer": "诊断回答文本",
  "evaluation": {
    "is_safe": true,
    "is_compliant": true,
    "confidence": 0.9,
    "issues": []
  },
  "sop": [
    "停机并断电，确认设备处于安全状态。"
  ],
  "llm_model": null,
  "llm_usage": null,
  "response": null,
  "errors": [],
  "warnings": []
}

设计原则：

State 中只保存 JSON 可序列化数据；
不在 State 中保存 service 实例；
ToolRegistry、TraceStore、MemoryStore、SandboxExecutor、Evaluator、LLMClient 通过 graph builder 注入；
TraceStore 保存完整执行细节；
MemoryStore 只保存多轮上下文摘要；
LangGraph checkpointer 负责图状态和 thread_id 关联。
7. API 设计
请求
POST /api/query
Content-Type: application/json
{
  "question": "生成 SQL 脚本检查诊断记录",
  "device_name": "摩托车发动机",
  "device_model": null,
  "session_id": "demo-session"
}
响应

对外统一使用 envelope：

{
  "success": true,
  "data": {
    "answer": "诊断回答文本",
    "plan": [],
    "evidence": [],
    "tool_calls": [],
    "evaluation": {
      "is_safe": true,
      "is_compliant": true,
      "confidence": 0.9,
      "issues": []
    },
    "trace_id": "uuid",
    "sop": [],
    "memory": [],
    "ai_coding": null,
    "llm_usage": null,
    "llm_model": null
  },
  "error": null,
  "trace_id": "http-request-trace-id"
}

说明：

data.trace_id：Agent Harness 执行 trace；
envelope 顶层 trace_id：HTTP 请求 trace；
memory：当前 session 的摘要历史；
ai_coding：只有触发 AI Coding 分支时返回；
llm_usage：DeepSeek live 调用时可返回，fallback 时允许为 null；
llm_model：DeepSeek 模型名，fallback 时可为空或标记 fallback。
8. 工具层设计

工具层通过 ToolRegistry 统一管理。

当前 MVP 工具：

manual_lookup
ai_coding
compliance_check
manual_lookup

用途：根据用户问题和设备信息检索维修手册 evidence。

当前状态：

使用 LlamaIndex 兼容 placeholder；
返回符合最终 EvidenceItem schema 的占位证据；
后续替换为真实 PDF chunk 检索。

输入示例：

{
  "question": "发动机无法启动怎么办",
  "device_name": "摩托车发动机",
  "device_model": null,
  "top_k": 5
}

输出示例：

[
  {
    "source": "manual::摩托车发动机",
    "page": null,
    "snippet": "当前为 LlamaIndex 兼容占位证据；后续将替换为真实的手册分块。",
    "score": 0.42,
    "metadata": {
      "retriever": "llama-index-placeholder"
    }
  }
]
ai_coding

用途：根据用户需求生成 Python / SQL 辅助脚本。

输出统一 schema：

{
  "language": "sql",
  "script": "SELECT * FROM diagnosis_records LIMIT 20;",
  "explanation": "用于检查诊断记录的查询脚本。",
  "warnings": []
}

约束：

只允许 python 和 sql；
不生成 shell；
不执行系统命令；
SQL 默认只允许 SELECT；
结果交给 sandbox_node 执行。
compliance_check

用途：检查回答是否包含必要安全提醒和合规要素。

检查项：

是否提示停机；
是否提示断电；
是否提示佩戴防护用品；
是否提醒核对设备型号；
是否避免直接执行高风险操作；
是否在证据不足时说明不确定性。
9. Sandbox 设计

当前 Sandbox 是比赛演示级受限执行器，不承诺强隔离。

支持范围
类型	当前策略
Shell	禁止执行
Python	AST 检查 + 隔离模式 + 超时 + 输出截断
SQL	内存 SQLite + 只允许 SELECT + 最多 20 行
Python 限制

禁止：

import
from import
open
exec
eval
compile
__import__
input
globals
locals
getattr
setattr
os
sys
subprocess
socket
shutil
pathlib
requests
urllib
multiprocessing
threading
ctypes
pickle
dunder 访问，如 __class__、__subclasses__、__globals__

允许常见安全内置函数：

print
len
range
sum
min
max
abs
round
sorted
enumerate
zip
str
int
float
bool
list
dict
set
tuple
SQL 限制

只允许：

SELECT ...

禁止：

INSERT
UPDATE
DELETE
DROP
ALTER
CREATE
ATTACH
DETACH
PRAGMA
VACUUM
REPLACE
TRUNCATE
10. Prompt 与 DeepSeek V4

LLM 层采用 DeepSeek V4，调用路径为：

LangGraph node
  |
  v
DeepSeekLLMClient
  |
  v
LangChain / OpenAI-compatible API
  |
  v
DeepSeek V4

当前设计：

默认模型：deepseek-v4-pro；
可通过环境变量切换 deepseek-v4-flash；
无 API Key 或调用失败时 fallback 到 deterministic 输出；
测试默认不依赖真实 API；
只有 RUN_LIVE_LLM_TESTS=1 且存在 DEEPSEEK_API_KEY 时才运行 live LLM 测试；
不记录、不返回 reasoning_content、thinking、chain_of_thought 等中间推理字段。
环境变量
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_THINKING_ENABLED=true
DEEPSEEK_REASONING_EFFORT=high
DEEPSEEK_TEMPERATURE=0.2
DEEPSEEK_MAX_TOKENS=2048
Prompt 文件
backend/app/prompts/
├── draft_answer_prompt.md
├── ai_coding_prompt.md
└── evaluator_prompt.md

当前职责：

Prompt	用途
draft_answer_prompt.md	基于 question、memory、evidence、tool_calls、sandbox_result 生成中文诊断回答
ai_coding_prompt.md	生成受限 Python / SQL 脚本
evaluator_prompt.md	后续 LLM evaluator 模板，当前主要使用本地 Evaluator
11. 前端展示设计

前端当前采用 React + Vite 三栏布局：

左侧：会话 / 手册 / 工单 / Artifact 导航
中间：用户问题 + Agent 回答
右侧：Artifact 工作区

右侧 Artifact 包含：

Tab	展示内容
SOP	sop、evaluation.confidence、evaluation.issues
证据	evidence.source/page/snippet/score/metadata
记录	trace_id、tool_calls、ai_coding、sandbox_result

当前前端通过相对路径调用：

POST /api/query

这意味着开发环境可通过 Vite proxy 转发到后端，避免前端写死 localhost:8000。

12. Skills 设计

当前仓库 MVP Skills 以实际目录为准，建议采用：

backend/app/skills/
├── fault_triage/
│   ├── AGENT.md
│   └── skill.md
├── sop_guidance/
│   ├── AGENT.md
│   └── skill.md
└── ai_coding/
    ├── AGENT.md
    ├── skill.md
    └── tool.py
当前 MVP Skills
Skill	当前职责
fault_triage	故障初诊、诊断假设、下一步排查
sop_guidance	根据 evidence 和 evaluation 生成作业步骤
ai_coding	根据用户需求生成受限 Python / SQL 脚本
后续扩展 Skills
backend/app/skills/
├── multimodal_retrieval/
├── compliance_review/
├── knowledge_curation/
├── demo_evaluator/
└── human_approval/

说明：

AGENT.md 描述该 Skill 的目标、工具、Plan、约束和评估标准；
skill.md 描述领域规则、模板和执行细节；
后续可按 Agent Skills 规范逐步统一命名。