# 三人分工与排期文档

## 1. 文档目的

本文件用于明确三位同学在当前项目中的职责边界、学习顺序、时间安排、改动文件范围和阶段验收标准。

当前日期按 **2026-05-06** 计算，比赛要求 **2026-06-30 前提交**。为了避免最后几天才联调和补材料，本计划设置三个关键节点：

- **2026-05-17：MVP 检索链路完成**，能从维修手册返回页码证据。
- **2026-06-14：功能冻结**，核心功能不再大改，只修 bug、补体验、补文档。
- **2026-06-24：提交包冻结**，源代码、文档、PPT、演示视频进入最终检查。

2026-06-25 到 2026-06-30 只做最终修正和提交，不再新增大功能。

## 2. 项目目标

第一阶段目标不是做完整工业平台，而是做出一个可以稳定演示、能覆盖赛题评分点的 MVP：

1. 导入《摩托车发动机维修手册.pdf》。
2. 能按页解析、分块、建立检索索引。
3. 用户输入故障问题后，系统返回相关页码、证据片段和初步诊断建议。
4. Agent 能展示 `plan -> retrieve -> evaluate -> answer` 的可解释流程。
5. 前端能展示查询、证据、计划步骤、SOP 作业卡、知识提交。
6. 文档、PPT、演示视频和部署材料完整，能支撑初赛提交。

## 3. 角色划分

| 角色 | 负责人 | 主要目标 | 主责目录 |
|---|---|---|---|
| 后端与 Agent | 同学 A | API、服务编排、Plan -> Execute Harness、SOP、知识提交接口、接口测试 | `backend/app/api/` `backend/app/schemas/` `backend/app/services/agent_harness.py` |
| 数据知识库与模型 | 同学 B | PDF 解析、分块、索引、检索、模型接入、知识图谱、评测集 | `data/` `backend/app/services/manual_indexer.py` `backend/app/services/retriever.py` |
| 前端与文档 | 同学 C | 页面、交互、接口联调、需求/设计/测试/部署文档、PPT、演示视频 | `frontend/` `docs/` `README.md` |

## 3.1 生产级 Agent 优化分工

当前主线从比赛 MVP 进入生产级 Agent 演进，Commit 8 到 Commit 13 的职责按以下方式分配：

| 阶段 | 目标 | 同学 A：后端与 Agent | 同学 B：数据知识库与模型 | 同学 C：前端与文档 |
|---|---|---|---|---|
| Commit 8 | Runtime Contract + Execution State Machine | 负责 `RuntimeStateFactory`、`RuntimeExecutor`、`RuntimeResultAdapter` 设计；接入取消、超时、max steps 和错误归一化 | 配合确认 retrieval、memory、sandbox 对 Runtime 的输入输出需求 | 更新 README、架构说明和演示脚本 |
| Commit 9 | Workflow 节点拆分 + RuntimeResult 输出 | 收敛 LangGraph 节点职责；让 graph 输出 `RuntimeResult`；补 Workflow 分支测试 | 对齐 evidence、tool、memory 字段，避免 worker 输出漂移 | 更新架构图、Trace 展示说明和文档截图 |
| Commit 10 | Tool / Worker / Policy 完整化 | 统一 ToolRegistry、WorkerDispatcher、policy adapter 和异常处理 | 负责 `ToolPolicy`、工具评测数据、retry / degraded / side-effect 策略 | 展示工具调用、审批、审计和 degraded 状态 |
| Commit 11 | Memory 持久化 + Retrieval + Fallback | Memory API 与 Runtime 集成；补 session、fallback、low confidence 测试 | 负责 PostgreSQL / Vector DB 方案、retrieval、eval、低置信度写入策略 | 更新产品说明、记忆能力说明和测试用例 |
| Commit 12 | Sandbox 安全隔离与后端插件化 | 定义 `SandboxBackend` 接口；接入 ToolPolicy、Runtime 和 API 错误处理 | 负责 Docker / Remote backend、资源限制、网络限制、沙箱评测 | 更新安全执行说明、风险边界和演示素材 |
| Commit 13 | E2E 测试 + Operational Metrics / Eval Feedback Loop | 负责端到端测试、Metrics 接口、CI 回归 | 负责 Eval regression dataset、失败归因、评测报告 | 负责 Operational dashboard、提交文档、PPT 和视频 |

执行原则：

1. Runtime / Workflow 优先，Tool、Memory、Sandbox 都必须向 Runtime Contract 对齐。
2. 所有 fallback、degraded、low confidence、approval、fail-safe 都必须进入 Trace 和 Metrics。
3. Trace / Eval / Metrics / Retention / Cleanup 已具备生产级基础，但访问控制、异步写入、dashboard 和 alerting 继续作为增强项推进。

## 4. 文件所有权

### 4.1 后端与 Agent 同学 A

优先负责：

- `backend/app/main.py`
- `backend/app/api/router.py`
- `backend/app/api/routes/health.py`
- `backend/app/api/routes/manuals.py`
- `backend/app/api/routes/query.py`
- `backend/app/schemas/manual.py`
- `backend/app/schemas/query.py`
- `backend/app/services/agent_harness.py`
- `backend/tests/test_health.py`

建议新增：

- `backend/app/api/routes/sop.py`
- `backend/app/api/routes/knowledge.py`
- `backend/app/schemas/sop.py`
- `backend/app/schemas/knowledge.py`
- `backend/app/services/sop_service.py`
- `backend/app/services/knowledge_service.py`
- `backend/app/services/tool_registry.py`
- `backend/app/services/evaluator.py`
- `backend/tests/test_manuals.py`
- `backend/tests/test_query.py`
- `backend/tests/test_sop.py`
- `backend/tests/test_knowledge.py`

### 4.2 数据知识库与模型同学 B

优先负责：

- `data/README.md`
- `data/raw/manuals/`
- `data/processed/`
- `data/indexes/`
- `data/uploads/`
- `backend/app/core/config.py`
- `backend/app/services/manual_indexer.py`
- `backend/app/services/retriever.py`

建议新增：

- `backend/app/services/pdf_parser.py`
- `backend/app/services/chunker.py`
- `backend/app/services/index_store.py`
- `backend/app/services/reranker.py`
- `backend/app/services/embedding_service.py`
- `backend/app/services/knowledge_graph.py`
- `backend/app/services/image_analyzer.py`
- `backend/tests/test_indexer.py`
- `backend/tests/test_retriever.py`
- `data/eval/questions.json`
- `data/eval/report.md`

### 4.3 前端与文档同学 C

优先负责：

- `frontend/package.json`
- `frontend/vite.config.ts`
- `frontend/src/main.tsx`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `docs/`
- `README.md`

建议新增：

- `frontend/src/pages/ManualRegisterPage.tsx`
- `frontend/src/pages/QueryPage.tsx`
- `frontend/src/pages/SopPage.tsx`
- `frontend/src/pages/KnowledgePage.tsx`
- `frontend/src/pages/EvaluationPage.tsx`
- `frontend/src/components/PlanTimeline.tsx`
- `frontend/src/components/EvidenceList.tsx`
- `frontend/src/components/QueryForm.tsx`
- `frontend/src/components/SopCard.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/types/api.ts`
- `docs/requirements_analysis.md`
- `docs/function_design.md`
- `docs/product_manual.md`
- `docs/test_report.md`
- `docs/deployment_guide.md`
- `docs/demo_script.md`
- `docs/ppt_outline.md`

## 5. 协作边界

1. `backend/app/api/` 和 `backend/app/schemas/` 默认由同学 A 改。
2. `backend/app/services/manual_indexer.py`、`retriever.py` 和数据处理服务默认由同学 B 改。
3. `frontend/` 和 `docs/` 默认由同学 C 改。
4. `backend/app/core/config.py` 是共享文件，但模型、索引、数据路径配置由同学 B 先设计，同学 A 只补 API 运行配置。
5. 接口字段变更必须由同学 A 先更新 schema，再通知同学 B 和同学 C。
6. 检索返回格式变更必须由同学 B 先给出样例 JSON，再由同学 A 接入，同学 C 展示。
7. 每周至少做一次完整联调，不能等到 6 月下旬再合并。

## 6. 总体里程碑

| 日期 | 里程碑 | 必须达到的效果 |
|---|---|---|
| 2026-05-10 | 项目初始化完成 | API、页面、数据目录、文档模板能并行开发 |
| 2026-05-17 | 检索 MVP 完成 | 输入故障问题，返回真实手册页码和证据片段 |
| 2026-05-24 | Agent 查询闭环完成 | 展示计划步骤、工具调用结果、证据和诊断草案 |
| 2026-05-31 | SOP 与知识提交完成 | 查询后能生成作业卡，案例能进入待审核 |
| 2026-06-07 | 多模态与模型增强完成 | 支持图片/设备型号输入的最小可演示能力 |
| 2026-06-14 | 功能冻结 | 核心功能完整，后续只修 bug、补体验、补文档 |
| 2026-06-21 | 部署与演示稿完成 | 可在目标环境或等价环境部署，PPT 和脚本成型 |
| 2026-06-24 | 提交包冻结 | 源码、文档、PPT、视频进入最终检查 |
| 2026-06-30 前 | 最终提交 | 提交材料完整，无缺项 |

## 7. 分周计划

## 第 1 周：2026-05-06 到 2026-05-10

本周目标：把项目从“骨架”推进到“可并行开发”的状态。

### 同学 A：后端与 Agent

本周先学：

- FastAPI 路由、请求体、响应体
- Pydantic schema
- `pytest` 和 FastAPI `TestClient`

本周要做：

1. 稳定 `manuals/register` 和 `query` 的请求响应字段。
2. 建立统一错误响应格式。
3. 给健康检查、手册注册、查询接口补基础测试。

本周改动文件：

- `backend/app/api/routes/manuals.py`
- `backend/app/api/routes/query.py`
- `backend/app/schemas/manual.py`
- `backend/app/schemas/query.py`
- `backend/tests/test_health.py`
- `backend/tests/test_manuals.py`
- `backend/tests/test_query.py`

本周预期效果：

- 后端接口合同稳定，前端和数据同学可以按固定 JSON 联调。

### 同学 B：数据知识库与模型

本周先学：

- `PyMuPDF` 或 `pdfplumber`
- PDF 页级解析
- chunk 元数据设计

本周要做：

1. 确认 `data/raw/manuals/摩托车发动机维修手册.pdf` 路径。
2. 新建 PDF 解析和 chunk 处理文件。
3. 设计解析输出格式：页码、章节、文本、图片占位、设备信息。

本周改动文件：

- `data/raw/manuals/摩托车发动机维修手册.pdf`
- `backend/app/services/pdf_parser.py`
- `backend/app/services/chunker.py`
- `backend/app/services/manual_indexer.py`
- `backend/app/core/config.py`

本周预期效果：

- 手册能被读取并输出结构化页数据。

### 同学 C：前端与文档

本周先学：

- React + Vite 项目结构
- 基础表单和接口调用
- 比赛文档目录结构

本周要做：

1. 将首页改成应用工作台，不做纯展示页。
2. 建立手册注册页和故障查询页。
3. 建立接口调用封装和 API 类型。
4. 建立文档模板。

本周改动文件：

- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `frontend/src/pages/ManualRegisterPage.tsx`
- `frontend/src/pages/QueryPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/types/api.ts`
- `docs/requirements_analysis.md`
- `docs/function_design.md`

本周预期效果：

- 前端能展示两个核心入口：手册注册和故障查询。

## 第 2 周：2026-05-11 到 2026-05-17

本周目标：打通“手册注册 -> 解析 -> 建索引 -> 查询证据”的 MVP 链路。

### 同学 A：后端与 Agent

本周先学：

- 服务层编排
- 接口异常处理
- API 日志

本周要做：

1. 接入同学 B 的 `ManualIndexer`。
2. 查询接口调用 `Retriever` 并返回真实证据。
3. `AgentHarness` 先实现轻量流程：归一化问题 -> 检索证据 -> 组织回答。

本周改动文件：

- `backend/app/api/routes/manuals.py`
- `backend/app/api/routes/query.py`
- `backend/app/services/agent_harness.py`
- `backend/app/schemas/query.py`
- `backend/tests/test_query.py`

本周预期效果：

- 用户输入故障问题后，后端返回真实页码和证据片段。

### 同学 B：数据知识库与模型

本周先学：

- BM25 或关键词检索
- JSON/SQLite 索引存储
- Recall@K 和 MRR

本周要做：

1. 完成页级解析。
2. 建立最小关键词索引。
3. `Retriever.search()` 返回 Top 3 到 Top 5 证据。
4. 建立 10 到 20 条测试问题。

本周改动文件：

- `backend/app/services/index_store.py`
- `backend/app/services/retriever.py`
- `data/processed/`
- `data/indexes/`
- `data/eval/questions.json`
- `backend/tests/test_retriever.py`

本周预期效果：

- 检索结果包含 `source`、`page`、`snippet`、`score`。

### 同学 C：前端与文档

本周先学：

- loading、error、empty state
- 证据列表和结果区设计
- 需求分析文档写法

本周要做：

1. 手册注册页能调接口。
2. 查询页能输入问题并展示证据。
3. 文档补业务背景、用户角色、核心流程。

本周改动文件：

- `frontend/src/pages/ManualRegisterPage.tsx`
- `frontend/src/pages/QueryPage.tsx`
- `frontend/src/components/EvidenceList.tsx`
- `frontend/src/lib/api.ts`
- `docs/requirements_analysis.md`
- `docs/function_design.md`

本周预期效果：

- 可以演示“输入问题 -> 返回证据”的第一条主流程。

## 第 3 周：2026-05-18 到 2026-05-24

本周目标：从普通检索升级为可解释 Agent 查询。

### 同学 A：后端与 Agent

本周先学：

- Plan -> Execute 工作流
- 工具注册表
- Guardrail 和 Evaluator 基本思想

本周要做：

1. `AgentHarness` 扩展为 `plan -> retrieve -> draft -> evaluate -> answer`。
2. 新建 `tool_registry.py`。
3. 新建 `evaluator.py`，检查是否有证据、是否缺少安全提醒。
4. 查询结果增加 plan、warnings、confidence。

本周改动文件：

- `backend/app/services/agent_harness.py`
- `backend/app/services/tool_registry.py`
- `backend/app/services/evaluator.py`
- `backend/app/schemas/query.py`
- `backend/tests/test_query.py`

本周预期效果：

- 前端能展示 Agent 计划，而不是只展示一段回答。

### 同学 B：数据知识库与模型

本周先学：

- 查询改写
- 向量检索基础
- 设备型号 alias 和部件词表

本周要做：

1. 给检索加入设备型号、部件、故障词元数据。
2. 设计向量检索接口，先保留本地或云端 embedding 可切换能力。
3. 增加简单 rerank。

本周改动文件：

- `backend/app/services/retriever.py`
- `backend/app/services/reranker.py`
- `backend/app/services/embedding_service.py`
- `backend/app/core/config.py`
- `data/indexes/`

本周预期效果：

- 检索结果比纯关键词稳定，能解释为什么命中。

### 同学 C：前端与文档

本周先学：

- 时间线组件
- 诊断结果信息结构
- 接口说明文档写法

本周要做：

1. 新增 `PlanTimeline` 展示 Agent 步骤。
2. 查询页分为答案、计划、证据、提醒四块。
3. 功能设计文档补模块图、接口字段、数据流。

本周改动文件：

- `frontend/src/components/PlanTimeline.tsx`
- `frontend/src/pages/QueryPage.tsx`
- `frontend/src/types/api.ts`
- `docs/function_design.md`

本周预期效果：

- 系统具备“Agent 可解释性”的演示点。

## 第 4 周：2026-05-25 到 2026-05-31

本周目标：补齐 SOP 作业指导和知识沉淀。

### 同学 A：后端与 Agent

本周先学：

- 结构化输出
- SOP 规则校验
- 审核流 API

本周要做：

1. 新建 SOP 接口和服务。
2. 新建知识提交与审核接口。
3. Agent 输出中加入“可进入作业指导”的判断。

本周改动文件：

- `backend/app/api/routes/sop.py`
- `backend/app/api/routes/knowledge.py`
- `backend/app/api/router.py`
- `backend/app/schemas/sop.py`
- `backend/app/schemas/knowledge.py`
- `backend/app/services/sop_service.py`
- `backend/app/services/knowledge_service.py`
- `backend/tests/test_sop.py`
- `backend/tests/test_knowledge.py`

本周预期效果：

- 查询结果可以生成步骤化作业卡，案例可以进入待审核。

### 同学 B：数据知识库与模型

本周先学：

- 知识图谱最小 schema
- 实体抽取
- 案例去重

本周要做：

1. 建立设备、部件、故障、症状、原因、作业步骤的最小关系结构。
2. 支持案例保存、状态变更、关系写入。
3. 产出一批演示用案例数据。

本周改动文件：

- `backend/app/services/knowledge_graph.py`
- `data/processed/`
- `data/uploads/`
- `data/eval/questions.json`
- `backend/tests/test_indexer.py`

本周预期效果：

- 系统开始覆盖赛题“知识沉淀与更新”要求。

### 同学 C：前端与文档

本周先学：

- SOP 卡片设计
- 上传表单
- 审核队列页面

本周要做：

1. 新增 SOP 页面。
2. 新增知识提交和审核页面。
3. 产品说明书写出主要功能使用流程。

本周改动文件：

- `frontend/src/pages/SopPage.tsx`
- `frontend/src/pages/KnowledgePage.tsx`
- `frontend/src/components/SopCard.tsx`
- `frontend/src/App.tsx`
- `docs/product_manual.md`

本周预期效果：

- 前端能演示“查询 -> SOP -> 知识提交”的闭环。

## 第 5 周：2026-06-01 到 2026-06-07

本周目标：补多模态、模型增强和评测面板。

### 同学 A：后端与 Agent

本周先学：

- 文件上传接口
- 多模态输入在 Agent 状态中的表示
- trace 记录

本周要做：

1. 查询接口支持图片文件 ID 或图片上传结果。
2. Agent 记录每次 plan、tool call、evidence、evaluation。
3. 增加 trace 查询接口，便于演示。

本周改动文件：

- `backend/app/api/routes/query.py`
- `backend/app/schemas/query.py`
- `backend/app/services/agent_harness.py`
- `backend/app/services/tool_registry.py`

本周预期效果：

- 系统可以展示一次完整 Agent 运行 trace。

### 同学 B：数据知识库与模型

本周先学：

- OCR 或视觉模型调用
- 图片描述转检索 query
- 混合检索评测

本周要做：

1. 新建 `image_analyzer.py`，支持故障图片生成文本描述或标签。
2. 将图片描述接入检索。
3. 对测试集跑一轮评测并生成报告。

本周改动文件：

- `backend/app/services/image_analyzer.py`
- `backend/app/services/retriever.py`
- `backend/app/services/index_store.py`
- `data/uploads/`
- `data/eval/report.md`

本周预期效果：

- 多模态能力有最小演示版本，评测指标有初稿。

### 同学 C：前端与文档

本周先学：

- 图片上传交互
- trace 可视化
- 测试报告写法

本周要做：

1. 查询页支持上传图片。
2. 新增评测页或评测结果展示区。
3. 测试报告写入检索指标、接口测试结果、功能测试用例。

本周改动文件：

- `frontend/src/pages/QueryPage.tsx`
- `frontend/src/pages/EvaluationPage.tsx`
- `frontend/src/components/EvidenceList.tsx`
- `docs/test_report.md`

本周预期效果：

- 多模态和评测成为可展示亮点，而不是口头描述。

## 第 6 周：2026-06-08 到 2026-06-14

本周目标：功能冻结，完成部署方案和稳定性处理。

### 同学 A：后端与 Agent

本周先学：

- 后端配置管理
- 生产启动方式
- 接口稳定性排查

本周要做：

1. 统一所有 API 返回格式。
2. 增加关键异常处理。
3. 完成后端启动说明和部署参数。
4. 只修影响演示的 bug，不再新增大功能。

本周改动文件：

- `backend/app/api/`
- `backend/app/core/config.py`
- `backend/app/services/agent_harness.py`
- `backend/tests/`

本周预期效果：

- 后端接口稳定，功能冻结。

### 同学 B：数据知识库与模型

本周先学：

- LoongArch/银河麒麟部署限制
- 依赖兼容性
- 索引文件迁移

本周要做：

1. 确定可部署的数据和模型方案。
2. 整理索引生成步骤。
3. 固定演示用测试数据。
4. 完成数据与模型部分部署说明。

本周改动文件：

- `backend/requirements.txt`
- `backend/app/core/config.py`
- `data/README.md`
- `docs/deployment_guide.md`
- `docs/function_design.md`

本周预期效果：

- 项目有明确的部署路径，不依赖临时手工操作。

### 同学 C：前端与文档

本周先学：

- 前端构建和部署
- 文档交付规范
- 页面异常态处理

本周要做：

1. 页面补 loading、错误、空状态。
2. 完成部署文档初稿。
3. 完成功能设计文档 80% 内容。
4. 整理截图素材。

本周改动文件：

- `frontend/src/`
- `frontend/package.json`
- `docs/function_design.md`
- `docs/deployment_guide.md`

本周预期效果：

- 系统进入功能冻结，文档进入补全阶段。

## 第 7 周：2026-06-15 到 2026-06-21

本周目标：联调、修 bug、完成主要文档和演示脚本。

### 同学 A：后端与 Agent

本周先学：

- 端到端联调排错
- 日志定位
- 测试覆盖补强

本周要做：

1. 补齐接口测试。
2. 配合同学 C 做完整用户流程联调。
3. 修复 Agent 输出不稳定、证据缺失、异常报错等问题。

本周改动文件：

- `backend/tests/`
- `backend/app/services/agent_harness.py`
- `backend/app/services/evaluator.py`
- `backend/app/api/`

本周预期效果：

- 一次完整演示流程可以连续跑完。

### 同学 B：数据知识库与模型

本周先学：

- 错误案例分析
- 检索参数调优
- 数据说明文档

本周要做：

1. 跑完测试问题集，记录命中率。
2. 调整 chunk、关键词、embedding、rerank 权重。
3. 输出数据知识库技术说明给同学 C 写文档和 PPT。

本周改动文件：

- `backend/app/services/retriever.py`
- `backend/app/services/reranker.py`
- `data/eval/report.md`
- `docs/test_report.md`

本周预期效果：

- 检索效果可量化，答辩时能说清楚技术方案和效果。

### 同学 C：前端与文档

本周先学：

- 演示脚本组织
- PPT 技术亮点表达
- 用户视角测试

本周要做：

1. 完成需求分析、功能设计、产品说明书、测试报告、部署文档。
2. 完成演示脚本。
3. 完成 PPT 大纲和主要页面。
4. 组织一次完整彩排。

本周改动文件：

- `docs/requirements_analysis.md`
- `docs/function_design.md`
- `docs/product_manual.md`
- `docs/test_report.md`
- `docs/deployment_guide.md`
- `docs/demo_script.md`
- `docs/ppt_outline.md`

本周预期效果：

- 主要提交文档基本完成，演示路线固定。

## 第 8 周：2026-06-22 到 2026-06-28

本周目标：提交包冻结，录制演示视频，做最终检查。

### 同学 A：后端与 Agent

本周要做：

1. 修复彩排中发现的后端和 Agent 问题。
2. 输出后端接口清单和启动命令。
3. 检查提交源码中没有无关临时文件和密钥。

本周改动文件：

- `backend/`
- `README.md`
- `docs/deployment_guide.md`

本周预期效果：

- 后端代码和部署说明可提交。

### 同学 B：数据知识库与模型

本周要做：

1. 固定演示索引和测试数据。
2. 确认手册、索引、评测结果能随提交包复现。
3. 给演示视频准备 3 到 5 个稳定问题。

本周改动文件：

- `data/`
- `docs/test_report.md`
- `docs/demo_script.md`

本周预期效果：

- 演示问题稳定，不临场碰运气。

### 同学 C：前端与文档

本周要做：

1. 完成 PPT。
2. 录制不超过 7 分钟的功能演示视频。
3. 按比赛要求核对所有提交材料。
4. 组织最终彩排。

本周改动文件：

- `frontend/`
- `docs/`
- `README.md`

本周预期效果：

- 提交包冻结，后续只允许修明显错误。

## 第 9 阶段：2026-06-29 到 2026-06-30

本阶段目标：最终提交，不再开发。

### 全员共同任务

1. 核对提交清单。
2. 在干净目录中按部署文档重跑一次。
3. 检查视频格式、时长、PPT、文档、源码是否齐全。
4. 在 2026-06-30 前完成提交。

本阶段预期效果：

- 所有材料完整提交，避免因为格式、缺文件、部署说明不清导致失分。

## 8. 提交材料责任人

| 材料 | 主责 | 配合 |
|---|---|---|
| 软件功能需求分析文档 | 同学 C | 同学 A、B 提供模块说明 |
| 软件功能设计文档 | 同学 C | 同学 A 提供接口与 Agent，B 提供数据与模型 |
| 软件产品说明书 | 同学 C | 全员检查流程 |
| 软件功能测试报告 | 同学 C | 同学 A 提供接口测试，B 提供检索评测 |
| 软件安装包及部署文档 | 同学 C | 同学 A、B 提供启动和依赖说明 |
| 软件源文件 | 同学 A | 同学 B、C 检查各自目录 |
| 功能演示 PPT | 同学 C | 同学 A、B 提供技术亮点 |
| 功能演示视频 | 同学 C | 全员参与彩排 |

## 9. 每天协作规则

1. 每天晚上固定 15 分钟同步：今天完成了什么、卡在哪、明天做什么。
2. 每周日晚上做一次里程碑验收，不达标就砍非核心功能。
3. 所有接口变更必须同步样例 JSON。
4. 所有模型和检索变更必须同步评测结果或至少同步前后对比样例。
5. 所有文档截图必须来自当前可运行版本，不能用过期页面。

## 10. 当前最先要做的事

从今天 **2026-05-06** 开始，三个人的第一步建议如下：

1. 同学 A：定稿 `manuals/register` 和 `query` 的请求响应字段，并补测试。
2. 同学 B：确认手册在 `data/raw/manuals/`，完成 PDF 页级解析。
3. 同学 C：把前端改成“手册注册页 + 故障查询页”，并建立文档模板。

第一优先级是 **2026-05-17 前完成检索 MVP**。只要这条链路跑通，后面的 Agent、SOP、多模态、知识沉淀才有稳定基础。
