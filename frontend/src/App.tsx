import { ChangeEvent, FormEvent, useRef, useState } from "react";

type IconName =
  | "attach"
  | "book"
  | "check"
  | "clipboard"
  | "file"
  | "filter"
  | "image"
  | "message"
  | "panel"
  | "plus"
  | "search"
  | "send"
  | "settings"
  | "upload"
  | "wrench";

type SceneKey = "maintenance" | "education";
type ArtifactTab = "path" | "evidence" | "log";
type WorkflowStatus = "idle" | "analyzing" | "retrieving" | "completed";
type FeedbackChoice = "accurate" | "inaccurate" | "supplement" | null;

type PromptCard = {
  title: string;
  detail: string;
  prompt: string;
  accent: "brick" | "sage" | "blue";
};

type SourceItem = {
  doc: string;
  locator: string;
  summary: string;
  confidence: number;
};

type SceneConfig = {
  label: string;
  badge: string;
  title: string;
  modes: string[];
  cards: PromptCard[];
  recent: Array<{ title: string; meta: string; active?: boolean }>;
  agents: string[];
  steps: string[];
  sources: SourceItem[];
  profile: {
    title: string;
    rows: Array<{ label: string; value: string }>;
  };
  tabs: Record<ArtifactTab, string>;
  inputPlaceholder: string;
  uploadDocLabel: string;
  uploadImageLabel: string;
  citeLabel: string;
  citeMockName: string;
  answer: string;
  artifactTitle: string;
  confidence: number;
  logs: Array<{ title: string; detail: string }>;
  outputs: string[];
  feedbackTitle: string;
  feedbackSavedText: string;
};

type ThreadItem = {
  id: number;
  prompt: string;
  response?: QueryResponse | null;
  error?: string | null;
  loading?: boolean;
};

type SelectedFile = {
  id: number;
  name: string;
  kind: "资料" | "图片" | "知识库引用";
  status: string;
};

type JsonMap = Record<string, unknown>;

type ApiEnvelope<T> = {
  success: boolean;
  data: T | null;
  error: {
    code: string;
    message: string;
    details?: unknown;
  } | null;
  trace_id: string;
};

type EvidenceItem = {
  source: string;
  page: number | null;
  snippet: string;
  score: number | null;
  metadata: JsonMap;
};

type PlanStep = {
  step: string;
  status: string;
};

type ToolCallItem = {
  tool_name: string;
  input: JsonMap;
  output: JsonMap | JsonMap[] | string | null;
  status: string;
  duration_ms: number | null;
};

type EvaluationResult = {
  is_safe: boolean;
  is_compliant: boolean;
  confidence: number;
  issues: string[];
};

type SandboxResult = {
  language: string;
  allowed: boolean;
  return_code: number | null;
  stdout: string;
  stderr: string;
  error: string | null;
  duration_ms: number | null;
};

type AICodingResult = {
  language?: string;
  script?: string;
  explanation?: string;
  warnings?: string[];
  sandbox_result?: SandboxResult | JsonMap | null;
  [key: string]: unknown;
};

type QueryResponse = {
  answer: string;
  plan: PlanStep[];
  evidence: EvidenceItem[];
  tool_calls: ToolCallItem[];
  evaluation: EvaluationResult | null;
  trace_id: string | null;
  sop: string[];
  memory: JsonMap[];
  ai_coding: AICodingResult | null;
  llm_usage: JsonMap | null;
  llm_model: string | null;
};

const sceneConfig: Record<SceneKey, SceneConfig> = {
  maintenance: {
    label: "设备检修助手",
    badge: "Equipment Maintenance Agent",
    title: "今天要排查哪类故障？",
    modes: ["诊断", "检索", "复核"],
    cards: [
      {
        title: "怠速不稳",
        detail: "生成排查顺序和证据页",
        prompt: "热车后怠速不稳，排气管偶尔回火，应该先检查哪里？",
        accent: "brick",
      },
      {
        title: "启动困难",
        detail: "定位燃油、点火、压缩链路",
        prompt: "冷车启动困难，启动机转速正常但发动机不着车，请给出排查流程。",
        accent: "sage",
      },
      {
        title: "异常噪声",
        detail: "按部件和工况拆解风险",
        prompt: "发动机加速时有金属敲击声，请结合手册给出可能原因和安全检查项。",
        accent: "blue",
      },
    ],
    recent: [
      { title: "怠速不稳和回火排查", meta: "2分钟前", active: true },
      { title: "机油压力灯异常", meta: "今天 14:20" },
      { title: "冷车启动困难", meta: "昨天" },
      { title: "气门间隙复检", meta: "周二" },
    ],
    agents: ["故障诊断 Agent", "手册检索 Agent", "SOP 生成 Agent", "安全校验 Agent"],
    steps: [
      "确认故障现象和设备运行状态",
      "检查相关管路、接口和关键部件",
      "对照手册定位可能故障原因",
      "生成标准化检修步骤",
      "复检并记录检修结果",
    ],
    sources: [
      {
        doc: "《摩托车发动机维修手册》",
        locator: "P.36",
        summary: "怠速调整、混合气状态和进气密封检查的标准顺序。",
        confidence: 91,
      },
      {
        doc: "《摩托车发动机维修手册》",
        locator: "P.58",
        summary: "火花塞颜色、积碳和电极间隙可辅助判断点火弱化。",
        confidence: 88,
      },
      {
        doc: "《检修案例沉淀记录》",
        locator: "Case 12",
        summary: "同类回火现象最终定位为进气歧管接口轻微漏气。",
        confidence: 84,
      },
    ],
    profile: {
      title: "当前设备",
      rows: [
        { label: "类型", value: "摩托车发动机" },
        { label: "型号", value: "示例型号" },
        { label: "故障等级", value: "中" },
        { label: "检修等级", value: "二级" },
      ],
    },
    tabs: { path: "SOP", evidence: "证据", log: "记录" },
    inputPlaceholder: "请输入故障现象、设备型号，或上传检修手册/故障图片。",
    uploadDocLabel: "上传检修手册",
    uploadImageLabel: "上传故障图片",
    citeLabel: "引用设备资料",
    citeMockName: "摩托车发动机维修手册 · P.36",
    answer:
      "我会先按“进气漏气、点火弱、怠速调整偏差”三个方向缩小范围。目前证据更指向进气系统密封和火花塞状态，建议先完成外观与接口检查，再决定是否拆检总成。",
    artifactTitle: "怠速不稳 SOP",
    confidence: 91,
    logs: [
      { title: "已召回 12 个高相关片段", detail: "过滤低置信证据 5 条" },
      { title: "已生成标准化检修步骤", detail: "等待维修员确认现场症状" },
      { title: "已预留经验补充入口", detail: "可沉淀检修案例到反馈标注" },
    ],
    outputs: ["SOP", "检修报告", "故障分析记录"],
    feedbackTitle: "经验补充 / 检修案例沉淀",
    feedbackSavedText: "反馈已记录，待审核纳入知识库",
  },
  education: {
    label: "个性化学习助手",
    badge: "Personalized Learning Agent",
    title: "今天想学习哪个知识点？",
    modes: ["画像", "讲解", "练习"],
    cards: [
      {
        title: "知识点讲解",
        detail: "拆解概念、公式与案例",
        prompt: "请用适合计算机专业学生的方式讲解反向传播，并给出一个小例子。",
        accent: "brick",
      },
      {
        title: "练习题生成",
        detail: "按目标难度生成题目和解析",
        prompt: "围绕神经网络基础生成 5 道由易到难的练习题，并附参考答案。",
        accent: "sage",
      },
      {
        title: "学习路径规划",
        detail: "结合基础水平安排学习顺序",
        prompt: "我想在两周内掌握人工智能核心知识点，请生成个性化学习路径。",
        accent: "blue",
      },
    ],
    recent: [
      { title: "反向传播知识点讲解", meta: "5分钟前", active: true },
      { title: "线性代数基础练习", meta: "今天 13:40" },
      { title: "机器学习学习路径", meta: "昨天" },
      { title: "Python 实操案例生成", meta: "周一" },
    ],
    agents: ["画像分析 Agent", "知识讲解 Agent", "题目生成 Agent", "路径规划 Agent"],
    steps: [
      "识别当前知识基础",
      "分析学习目标和薄弱知识点",
      "推荐核心学习资料",
      "生成练习题和实操案例",
      "根据反馈调整学习路径",
    ],
    sources: [
      {
        doc: "《人工智能导论课件》",
        locator: "Chapter 3",
        summary: "神经网络结构、损失函数和反向传播的核心脉络。",
        confidence: 88,
      },
      {
        doc: "《神经网络基础讲义》",
        locator: "P.12",
        summary: "链式法则、梯度计算和参数更新的入门说明。",
        confidence: 86,
      },
      {
        doc: "《课程练习题库》",
        locator: "Unit 2",
        summary: "适合中等基础学生的概念题、计算题和编程题。",
        confidence: 82,
      },
    ],
    profile: {
      title: "当前学习者",
      rows: [
        { label: "专业", value: "计算机" },
        { label: "课程", value: "人工智能" },
        { label: "基础水平", value: "中等" },
        { label: "学习目标", value: "掌握核心知识点" },
      ],
    },
    tabs: { path: "学习路径", evidence: "资料依据", log: "学习记录" },
    inputPlaceholder: "请输入课程、知识点、学习目标，或上传课件/题目/学习资料。",
    uploadDocLabel: "上传课件资料",
    uploadImageLabel: "上传题目图片",
    citeLabel: "引用课程知识库",
    citeMockName: "人工智能导论课件 · Chapter 3",
    answer:
      "我会先识别你的基础水平和目标，再从课程资料中提取关键概念。当前更适合先把链式法则、损失函数和参数更新串起来，再进入反向传播的计算练习。",
    artifactTitle: "反向传播学习路径",
    confidence: 88,
    logs: [
      { title: "已召回 9 个课程片段", detail: "覆盖课件、讲义和题库" },
      { title: "已生成分层学习路径", detail: "包含讲解、练习和复盘节点" },
      { title: "已预留学习反馈入口", detail: "可用于内容修正和路径优化" },
    ],
    outputs: ["学习路径", "讲解文档", "练习题", "思维导图", "PPT 大纲"],
    feedbackTitle: "学习反馈 / 内容修正",
    feedbackSavedText: "反馈已保存，用于后续优化",
  },
};

const navItems: Array<{
  icon: IconName;
  label: string;
  active?: boolean;
  count?: string;
}> = [
  { icon: "message", label: "智能对话", active: true, count: "12" },
  { icon: "book", label: "知识库", count: "3" },
  { icon: "clipboard", label: "任务流程" },
  { icon: "panel", label: "生成成果" },
  { icon: "check", label: "反馈标注" },
];

const tabOrder: ArtifactTab[] = ["path", "evidence", "log"];

const workflowLabels: Record<WorkflowStatus, string> = {
  idle: "已完成",
  analyzing: "分析中",
  retrieving: "检索中",
  completed: "已完成",
};

function Icon({ name }: { name: IconName }) {
  const props = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "attach":
      return (
        <svg {...props}>
          <path d="M21 11.5 12.7 19.8a6 6 0 0 1-8.5-8.5l8.9-8.9a4 4 0 1 1 5.7 5.7L9.9 17a2 2 0 1 1-2.8-2.8l8.3-8.3" />
        </svg>
      );
    case "book":
      return (
        <svg {...props}>
          <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v17H6.5A2.5 2.5 0 0 1 4 17.5v-12Z" />
          <path d="M4 17.5A2.5 2.5 0 0 1 6.5 15H20" />
        </svg>
      );
    case "check":
      return (
        <svg {...props}>
          <path d="m5 12 4 4L19 6" />
        </svg>
      );
    case "clipboard":
      return (
        <svg {...props}>
          <path d="M9 4h6l1 2h3v15H5V6h3l1-2Z" />
          <path d="M9 12h6" />
          <path d="M9 16h4" />
        </svg>
      );
    case "file":
      return (
        <svg {...props}>
          <path d="M6 3h8l4 4v14H6V3Z" />
          <path d="M14 3v5h4" />
          <path d="M9 13h6" />
          <path d="M9 17h4" />
        </svg>
      );
    case "filter":
      return (
        <svg {...props}>
          <path d="M4 6h16" />
          <path d="M7 12h10" />
          <path d="M10 18h4" />
        </svg>
      );
    case "image":
      return (
        <svg {...props}>
          <rect x="4" y="5" width="16" height="14" rx="2" />
          <circle cx="9" cy="10" r="1.5" />
          <path d="m7 17 4.2-4.2a1.5 1.5 0 0 1 2.1 0L18 17" />
        </svg>
      );
    case "message":
      return (
        <svg {...props}>
          <path d="M4 5h16v11H8l-4 4V5Z" />
        </svg>
      );
    case "panel":
      return (
        <svg {...props}>
          <path d="M4 5h16v14H4V5Z" />
          <path d="M14 5v14" />
        </svg>
      );
    case "plus":
      return (
        <svg {...props}>
          <path d="M12 5v14" />
          <path d="M5 12h14" />
        </svg>
      );
    case "search":
      return (
        <svg {...props}>
          <circle cx="11" cy="11" r="6" />
          <path d="m16 16 4 4" />
        </svg>
      );
    case "send":
      return (
        <svg {...props}>
          <path d="M21 3 10 14" />
          <path d="m21 3-7 18-4-7-7-4 18-7Z" />
        </svg>
      );
    case "settings":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3" />
          <path d="M12 19v3" />
          <path d="m4.2 4.2 2.1 2.1" />
          <path d="m17.7 17.7 2.1 2.1" />
          <path d="M2 12h3" />
          <path d="M19 12h3" />
          <path d="m4.2 19.8 2.1-2.1" />
          <path d="m17.7 6.3 2.1-2.1" />
        </svg>
      );
    case "upload":
      return (
        <svg {...props}>
          <path d="M12 16V4" />
          <path d="m7 9 5-5 5 5" />
          <path d="M4 16v4h16v-4" />
        </svg>
      );
    case "wrench":
      return (
        <svg {...props}>
          <path d="M14.7 6.3a4.5 4.5 0 0 0 5.1 5.1L11 20.2 6.8 16l8.8-8.8Z" />
          <path d="m6.8 16-3 3 1.2 1.2 3-3" />
        </svg>
      );
    default:
      return null;
  }
}

function getAgentState(
  status: WorkflowStatus,
  index: number,
): "done" | "active" | "pending" {
  if (status === "completed" || status === "idle") {
    return "done";
  }

  const activeIndex = status === "analyzing" ? 0 : 1;

  if (index < activeIndex) {
    return "done";
  }

  return index === activeIndex ? "active" : "pending";
}

function formatJson(value: unknown) {
  if (value === null || value === undefined) {
    return "null";
  }

  if (typeof value === "string") {
    return value;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatConfidence(confidence?: number | null, fallback = 91) {
  if (typeof confidence !== "number" || Number.isNaN(confidence)) {
    return fallback;
  }

  const normalized = confidence <= 1 ? confidence * 100 : confidence;
  return Math.max(0, Math.min(100, Math.round(normalized)));
}

function formatScore(score?: number | null) {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return "未知";
  }

  const normalized = score <= 1 ? score * 100 : score;
  return `${Math.round(normalized)}%`;
}

function evidencePageLabel(page?: number | null) {
  return page === null || page === undefined ? "P.-" : `P.${page}`;
}

function hasMetadata(metadata: JsonMap) {
  return Object.keys(metadata).length > 0;
}

function App() {
  const [scene, setScene] = useState<SceneKey>("maintenance");
  const config = sceneConfig[scene];
  const [draft, setDraft] = useState(config.cards[0].prompt);
  const [thread, setThread] = useState<ThreadItem[]>([
    { id: Date.now(), prompt: config.cards[0].prompt, response: null },
  ]);
  const [activeTab, setActiveTab] = useState<ArtifactTab>("path");
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus>("completed");
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([]);
  const [feedbackChoice, setFeedbackChoice] = useState<FeedbackChoice>(null);
  const [showCorrection, setShowCorrection] = useState(false);
  const [correctionText, setCorrectionText] = useState("");
  const [feedbackNotice, setFeedbackNotice] = useState("");
  const docInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const latestThreadItem = thread[thread.length - 1];
  const latestResponse = latestThreadItem?.response ?? null;
  const latestError = latestThreadItem?.error ?? null;
  const isBackendLoading = Boolean(latestThreadItem?.loading);
  const confidence = formatConfidence(
    latestResponse?.evaluation?.confidence,
    config.confidence,
  );
  const displayedSteps = latestResponse?.sop?.length
    ? latestResponse.sop
    : latestResponse?.plan?.length
      ? latestResponse.plan.map((item) => item.step)
      : config.steps;
  const displayedSources = latestResponse?.evidence?.length
    ? latestResponse.evidence.map((item) => ({
        doc: item.source,
        locator: evidencePageLabel(item.page),
        summary: item.snippet,
        confidence: formatConfidence(item.score, config.confidence),
      }))
    : config.sources;
  const displayedLogs = latestResponse?.tool_calls?.length
    ? latestResponse.tool_calls.map((call) => ({
        title: call.tool_name,
        detail: `${call.status}${call.duration_ms !== null ? ` · ${call.duration_ms} ms` : ""}`,
      }))
    : config.logs;

  function resetFeedback() {
    setFeedbackChoice(null);
    setShowCorrection(false);
    setCorrectionText("");
    setFeedbackNotice("");
  }

  function handleSceneChange(nextScene: SceneKey) {
    const nextConfig = sceneConfig[nextScene];

    setScene(nextScene);
    setDraft(nextConfig.cards[0].prompt);
    setThread([{ id: Date.now(), prompt: nextConfig.cards[0].prompt, response: null }]);
    setActiveTab("path");
    setWorkflowStatus("completed");
    setSelectedFiles([]);
    resetFeedback();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextPrompt = draft.trim();

    if (!nextPrompt || isBackendLoading) {
      return;
    }

    const nextId = Date.now();

    setThread((current) => [
      ...current,
      { id: nextId, prompt: nextPrompt, response: null, error: null, loading: true },
    ]);
    setWorkflowStatus("analyzing");
    resetFeedback();

    window.setTimeout(() => setWorkflowStatus("retrieving"), 450);

    try {
      const httpResponse = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: nextPrompt,
          device_name:
            scene === "maintenance"
              ? config.profile.rows.find((row) => row.label === "类型")?.value
              : undefined,
          device_model:
            scene === "maintenance"
              ? config.profile.rows.find((row) => row.label === "型号")?.value
              : undefined,
          session_id: `demo-${scene}`,
        }),
      });
      const envelope = (await httpResponse.json()) as ApiEnvelope<QueryResponse>;

      if (!httpResponse.ok || !envelope.success || !envelope.data) {
        throw new Error(envelope.error?.message || `请求失败：${httpResponse.status}`);
      }

      setThread((current) =>
        current.map((item) =>
          item.id === nextId
            ? { ...item, response: envelope.data, loading: false, error: null }
            : item,
        ),
      );
    } catch (caught) {
      setThread((current) =>
        current.map((item) =>
          item.id === nextId
            ? {
                ...item,
                loading: false,
                error: caught instanceof Error ? caught.message : "调用 Harness 失败",
              }
            : item,
        ),
      );
    } finally {
      setWorkflowStatus("completed");
    }
  }

  function handleFileChange(
    event: ChangeEvent<HTMLInputElement>,
    kind: "资料" | "图片",
  ) {
    const files = Array.from(event.target.files ?? []);

    if (!files.length) {
      return;
    }

    setSelectedFiles((current) => [
      ...current,
      ...files.map((file) => ({
        id: Date.now() + Math.random(),
        name: file.name,
        kind,
        status: "待解析",
      })),
    ]);
    event.target.value = "";
  }

  function handleCiteKnowledge() {
    setSelectedFiles((current) => [
      ...current,
      {
        id: Date.now(),
        name: config.citeMockName,
        kind: "知识库引用",
        status: "已引用",
      },
    ]);
  }

  function handleFeedback(nextChoice: FeedbackChoice) {
    setFeedbackChoice(nextChoice);
    setFeedbackNotice("");

    if (nextChoice === "accurate") {
      setShowCorrection(false);
      setFeedbackNotice(config.feedbackSavedText);
      return;
    }

    setShowCorrection(true);
  }

  function handleCorrectionSubmit() {
    setShowCorrection(false);
    setCorrectionText("");
    setFeedbackNotice(config.feedbackSavedText);
  }

  return (
    <div className="workspace">
      <aside className="sidebar" aria-label="主导航">
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">
            智
          </div>
          <div className="brand-copy">
            <span>多模态知识智能体平台</span>
            <strong>智源 Agent</strong>
          </div>
        </div>

        <button className="new-chat-button" type="button" onClick={() => handleSceneChange(scene)}>
          <Icon name="plus" />
          <span>新会话</span>
        </button>

        <label className="sidebar-search">
          <Icon name="search" />
          <input aria-label="搜索" placeholder="搜索对话、知识库、任务流程" />
        </label>

        <nav className="nav-stack" aria-label="功能">
          {navItems.map((item) => (
            <button
              className={`nav-item${item.active ? " is-active" : ""}`}
              key={item.label}
              type="button"
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.count ? <small>{item.count}</small> : null}
            </button>
          ))}
        </nav>

        <section className="conversation-list" aria-labelledby="recent-title">
          <div className="section-heading">
            <h2 id="recent-title">最近</h2>
            <button aria-label="筛选最近对话" className="icon-button" type="button">
              <Icon name="filter" />
            </button>
          </div>
          {config.recent.map((conversation) => (
            <button
              className={`conversation-item${
                conversation.active ? " is-active" : ""
              }`}
              key={conversation.title}
              onClick={() => setDraft(conversation.title)}
              type="button"
            >
              <span>{conversation.title}</span>
              <small>{conversation.meta}</small>
            </button>
          ))}
        </section>

        <div className="sidebar-footer">
          <div className="user-avatar" aria-hidden="true">
            L
          </div>
          <div>
            <span>演示工作台</span>
            <small>比赛通用前端底座</small>
          </div>
          <button aria-label="设置" className="icon-button" type="button">
            <Icon name="settings" />
          </button>
        </div>
      </aside>

      <main className="chat-shell">
        <header className="chat-topbar">
          <div className="scene-switch" aria-label="场景选择">
            {(["maintenance", "education"] as SceneKey[]).map((sceneKey) => (
              <button
                className={scene === sceneKey ? "is-active" : ""}
                key={sceneKey}
                onClick={() => handleSceneChange(sceneKey)}
                type="button"
              >
                {sceneConfig[sceneKey].label}
              </button>
            ))}
          </div>

          <div className="mode-switch" aria-label="工作模式">
            {config.modes.map((mode, index) => (
              <button className={index === 0 ? "is-active" : ""} key={mode} type="button">
                {mode}
              </button>
            ))}
          </div>
        </header>

        <section className="welcome-block">
          <p className="eyebrow">Multi-Agent Knowledge Assistant · {config.badge}</p>
          <h1>{config.title}</h1>
          <div className="prompt-grid">
            {config.cards.map((card) => (
              <button
                className={`prompt-card accent-${card.accent}`}
                key={card.title}
                onClick={() => setDraft(card.prompt)}
                type="button"
              >
                <span>{card.title}</span>
                <small>{card.detail}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="thread" aria-label="对话内容">
          {thread.map((item, index) => {
            const isLatest = index === thread.length - 1;
            const answerText = item.loading
              ? "正在调用 Harness..."
              : item.error
                ? `接口联调失败：${item.error}。已保留当前场景 mock 结果用于展示。`
                : item.response?.answer || config.answer;
            const inlineSources = item.response?.evidence?.length
              ? item.response.evidence.slice(0, 2).map((source) => ({
                  key: `${source.source}-${source.page ?? "none"}`,
                  locator: evidencePageLabel(source.page),
                }))
              : config.sources.slice(0, 2).map((source) => ({
                  key: `${source.doc}-${source.locator}`,
                  locator: source.locator,
                }));
            const ragSources = item.response?.evidence?.length
              ? item.response.evidence.slice(0, 2).map((source) => ({
                  doc: source.source,
                  locator: evidencePageLabel(source.page),
                  summary: source.snippet,
                  confidence: formatConfidence(source.score, config.confidence),
                }))
              : config.sources.slice(0, 2);

            return (
              <div className="thread-exchange" key={item.id}>
                <article className="message-row is-user">
                  <div className="message-bubble">{item.prompt}</div>
                </article>

                <article className="message-row is-assistant">
                  <div className="assistant-avatar" aria-hidden="true">
                    A
                  </div>
                  <div className="assistant-message">
                    <div className="agent-flow">
                      <div className="assistant-section-title">
                        <span>智能体协作流程</span>
                        <small>{isLatest ? workflowLabels[workflowStatus] : "已完成"}</small>
                      </div>
                      <div className="agent-steps">
                        {config.agents.map((agent, agentIndex) => {
                          const state = isLatest
                            ? getAgentState(workflowStatus, agentIndex)
                            : "done";

                          return (
                            <span className={`agent-step is-${state}`} key={agent}>
                              {agent}
                            </span>
                          );
                        })}
                      </div>
                    </div>

                    <p className={item.error ? "assistant-error" : undefined}>{answerText}</p>

                    <div className="rag-preview">
                      <div className="assistant-section-title">
                        <span>RAG 来源依据</span>
                        <small>{item.response ? "接口返回" : "Mock 检索结果"}</small>
                      </div>
                      <div className="rag-cards">
                        {ragSources.map((source) => (
                          <article className="rag-card" key={`${source.doc}-${source.locator}`}>
                            <strong>
                              {source.doc} {source.locator}
                            </strong>
                            <span>{source.summary}</span>
                            <small>置信度 {source.confidence}%</small>
                          </article>
                        ))}
                      </div>
                    </div>

                    <div className="inline-evidence">
                      {inlineSources.map((source) => (
                        <button key={source.key} type="button">
                          <Icon name="file" />
                          <span>{source.locator}</span>
                        </button>
                      ))}
                    </div>

                    {isLatest ? (
                      <div className="feedback-box">
                        <div className="assistant-section-title">
                          <span>{config.feedbackTitle}</span>
                          <small>反馈标注</small>
                        </div>
                        <div className="feedback-actions">
                          <button
                            className={feedbackChoice === "accurate" ? "is-active" : ""}
                            onClick={() => handleFeedback("accurate")}
                            type="button"
                          >
                            准确
                          </button>
                          <button
                            className={feedbackChoice === "inaccurate" ? "is-active" : ""}
                            onClick={() => handleFeedback("inaccurate")}
                            type="button"
                          >
                            不准确
                          </button>
                          <button
                            className={feedbackChoice === "supplement" ? "is-active" : ""}
                            onClick={() => handleFeedback("supplement")}
                            type="button"
                          >
                            需要补充
                          </button>
                          <button onClick={() => setShowCorrection(true)} type="button">
                            提交修正
                          </button>
                        </div>

                        {showCorrection ? (
                          <div className="correction-panel">
                            <textarea
                              aria-label="反馈修正"
                              onChange={(event) => setCorrectionText(event.target.value)}
                              placeholder="请输入正确答案、补充说明或经验记录"
                              rows={3}
                              value={correctionText}
                            />
                            <button onClick={handleCorrectionSubmit} type="button">
                              提交反馈
                            </button>
                          </div>
                        ) : null}

                        {feedbackNotice ? (
                          <p className="feedback-notice">{feedbackNotice}</p>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </article>
              </div>
            );
          })}
        </section>

        <form className="composer" onSubmit={handleSubmit}>
          <input
            accept=".pdf,.doc,.docx,.ppt,.pptx,.txt,.md"
            aria-label={config.uploadDocLabel}
            hidden
            multiple
            onChange={(event) => handleFileChange(event, "资料")}
            ref={docInputRef}
            type="file"
          />
          <input
            accept="image/*"
            aria-label={config.uploadImageLabel}
            hidden
            multiple
            onChange={(event) => handleFileChange(event, "图片")}
            ref={imageInputRef}
            type="file"
          />

          <div className="composer-input">
            <button
              aria-label="添加资料"
              className="icon-button"
              onClick={() => docInputRef.current?.click()}
              type="button"
            >
              <Icon name="attach" />
            </button>
            <textarea
              aria-label="输入问题"
              onChange={(event) => setDraft(event.target.value)}
              placeholder={config.inputPlaceholder}
              rows={1}
              value={draft}
            />
          </div>

          {selectedFiles.length ? (
            <div className="file-chip-list" aria-label="已选择资料">
              {selectedFiles.map((file) => (
                <span className="file-chip" key={file.id}>
                  <Icon name={file.kind === "图片" ? "image" : "file"} />
                  <strong>{file.name}</strong>
                  <small>
                    {file.kind} · {file.status}
                  </small>
                </span>
              ))}
            </div>
          ) : null}

          <div className="composer-actions">
            <button
              className="tool-button"
              onClick={() => docInputRef.current?.click()}
              type="button"
            >
              <Icon name="upload" />
              <span>{config.uploadDocLabel}</span>
            </button>
            <button
              className="tool-button"
              onClick={() => imageInputRef.current?.click()}
              type="button"
            >
              <Icon name="image" />
              <span>{config.uploadImageLabel}</span>
            </button>
            <button className="tool-button" onClick={handleCiteKnowledge} type="button">
              <Icon name="book" />
              <span>{config.citeLabel}</span>
            </button>
            <button
              aria-label="发送"
              className="send-button"
              disabled={isBackendLoading}
              type="submit"
            >
              <Icon name="send" />
            </button>
          </div>
        </form>
      </main>

      <aside className="artifact-shell" aria-label="智能体工作区">
        <header className="artifact-header">
          <div>
            <p>Artifact</p>
            <h2>{latestResponse?.ai_coding ? "AI Coding 记录" : config.artifactTitle}</h2>
          </div>
          <button aria-label="展开工作区" className="icon-button" type="button">
            <Icon name="panel" />
          </button>
        </header>

        <section className="profile-card" aria-label={config.profile.title}>
          <div className="profile-card-title">
            <span>当前对象</span>
            <strong>{config.profile.title}</strong>
          </div>
          <dl>
            {config.profile.rows.map((row) => (
              <div key={row.label}>
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
        </section>

        <div className="artifact-tabs" role="tablist" aria-label="工作区视图">
          {tabOrder.map((tab) => (
            <button
              aria-selected={activeTab === tab}
              className={activeTab === tab ? "is-active" : ""}
              key={tab}
              onClick={() => setActiveTab(tab)}
              role="tab"
              type="button"
            >
              {config.tabs[tab]}
            </button>
          ))}
        </div>

        {activeTab === "path" ? (
          <section className="artifact-content">
            <div className="manual-visual" aria-hidden="true">
              <div className="manual-page">
                <span />
                <span />
                <span />
              </div>
              <div className="engine-diagram">
                <i className="engine-core" />
                <i className="engine-port left" />
                <i className="engine-port right" />
                <i className="engine-line first" />
                <i className="engine-line second" />
              </div>
            </div>

            <div className="risk-strip">
              <span>可信度</span>
              <strong>{confidence}%</strong>
              <div className="confidence-track">
                <i style={{ width: `${confidence}%` }} />
              </div>
            </div>

            {latestResponse?.evaluation?.issues?.length ? (
              <div className="risk-issues">
                <strong>风险提示</strong>
                {latestResponse.evaluation.issues.map((issue) => (
                  <span key={issue}>{issue}</span>
                ))}
              </div>
            ) : null}

            <ol className="sop-list">
              {displayedSteps.map((step, index) => (
                <li key={`${step}-${index}`}>
                  <span>{index + 1}</span>
                  <p>{step}</p>
                </li>
              ))}
            </ol>

            <section className="output-preview" aria-labelledby="output-preview-title">
              <div className="assistant-section-title">
                <span id="output-preview-title">生成成果</span>
                <small>示例入口</small>
              </div>
              <div className="output-tags">
                {config.outputs.map((output) => (
                  <button key={output} type="button">
                    {output}
                  </button>
                ))}
              </div>
            </section>
          </section>
        ) : null}

        {activeTab === "evidence" ? (
          <section className="artifact-content">
            <div className="evidence-stack">
              {latestResponse?.evidence?.length
                ? latestResponse.evidence.map((source, index) => (
                    <article className="evidence-row" key={`${source.source}-${index}`}>
                      <Icon name="file" />
                      <div>
                        <strong>{source.source}</strong>
                        <span>
                          {evidencePageLabel(source.page)} · 匹配度 {formatScore(source.score)}
                        </span>
                        <p className="artifact-copy">{source.snippet}</p>
                        {hasMetadata(source.metadata) ? (
                          <pre className="json-snippet">{formatJson(source.metadata)}</pre>
                        ) : null}
                      </div>
                    </article>
                  ))
                : displayedSources.map((source) => (
                    <article className="evidence-row" key={`${source.doc}-${source.locator}`}>
                      <Icon name="file" />
                      <div>
                        <strong>
                          {source.doc} {source.locator}
                        </strong>
                        <span>{source.summary}</span>
                        <small>相似度 / 置信度 {source.confidence}%</small>
                      </div>
                    </article>
                  ))}
            </div>
          </section>
        ) : null}

        {activeTab === "log" ? (
          <section className="artifact-content">
            <div className="log-timeline">
              {latestResponse?.trace_id ? (
                <article>
                  <Icon name="check" />
                  <div>
                    <strong>Trace ID</strong>
                    <span>{latestResponse.trace_id}</span>
                  </div>
                </article>
              ) : null}

              {latestResponse?.llm_model || latestResponse?.llm_usage ? (
                <article>
                  <Icon name="check" />
                  <div>
                    <strong>LLM 调用信息</strong>
                    <span>{latestResponse.llm_model || "模型未返回"}</span>
                    {latestResponse.llm_usage ? (
                      <pre className="json-snippet">{formatJson(latestResponse.llm_usage)}</pre>
                    ) : null}
                  </div>
                </article>
              ) : null}

              {latestResponse?.evaluation ? (
                <article>
                  <Icon name="check" />
                  <div>
                    <strong>评估结果 · 可信度 {confidence}%</strong>
                    <span>
                      安全：{latestResponse.evaluation.is_safe ? "通过" : "需复核"} · 合规：
                      {latestResponse.evaluation.is_compliant ? "通过" : "需复核"}
                    </span>
                    {latestResponse.evaluation.issues.length ? (
                      <p className="artifact-copy">
                        风险提示：{latestResponse.evaluation.issues.join("；")}
                      </p>
                    ) : null}
                  </div>
                </article>
              ) : null}

              {latestResponse?.tool_calls?.length
                ? latestResponse.tool_calls.map((call, index) => (
                    <article key={`${call.tool_name}-${index}`}>
                      <Icon name="check" />
                      <div>
                        <strong>{call.tool_name}</strong>
                        <span>
                          {call.status}
                          {call.duration_ms !== null ? ` · ${call.duration_ms} ms` : ""}
                        </span>
                        <details>
                          <summary>输入 / 输出</summary>
                          <pre className="json-snippet">
                            {`input:\n${formatJson(call.input)}\n\noutput:\n${formatJson(call.output)}`}
                          </pre>
                        </details>
                      </div>
                    </article>
                  ))
                : displayedLogs.map((log) => (
                    <article key={log.title}>
                      <Icon name="check" />
                      <div>
                        <strong>{log.title}</strong>
                        <span>{log.detail}</span>
                      </div>
                    </article>
                  ))}

              {latestError ? (
                <article>
                  <Icon name="check" />
                  <div>
                    <strong>接口联调状态</strong>
                    <span>{latestError}</span>
                  </div>
                </article>
              ) : null}

              {latestResponse?.ai_coding ? (
                <article className="ai-coding-card">
                  <Icon name="file" />
                  <div>
                    <strong>AI Coding · {latestResponse.ai_coding.language || "unknown"}</strong>
                    {latestResponse.ai_coding.explanation ? (
                      <span>{latestResponse.ai_coding.explanation}</span>
                    ) : null}
                    {latestResponse.ai_coding.script ? (
                      <pre className="code-snippet">{latestResponse.ai_coding.script}</pre>
                    ) : null}
                    {latestResponse.ai_coding.warnings?.length ? (
                      <p className="artifact-copy">
                        提示：{latestResponse.ai_coding.warnings.join("；")}
                      </p>
                    ) : null}
                    {latestResponse.ai_coding.sandbox_result ? (
                      <details open>
                        <summary>sandbox_result</summary>
                        <pre className="json-snippet">
                          {formatJson(latestResponse.ai_coding.sandbox_result)}
                        </pre>
                      </details>
                    ) : null}
                  </div>
                </article>
              ) : null}
            </div>
          </section>
        ) : null}
      </aside>
    </div>
  );
}

export default App;
