# BugDoctor

> 假设驱动的 Bug 诊断 Agent —— 自动阅读代码库、提出假设、用工具验证、输出根因分析。

BugDoctor 是一个**结构化 ReAct 智能体**，专注于软件 Bug 诊断。给它一段报错信息或 Bug 描述，它会自动探索项目代码、阅读相关文件、搜索关键符号、运行诊断命令，反复迭代假设直到定位根因，最后给出修复建议。

与业界常见的 prompt 级 ReAct（用正则解析 LLM 文本输出）不同，BugDoctor 采用**代码驱动的 while 循环 + 原生 tool calling**。每一次工具调用、执行结果和推理步骤，都是类型化事件管道中的结构化数据。

---

## 目录

- [特性](#特性)
- [架构概览](#架构概览)
- [快速开始](#快速开始)
- [安装](#安装)
- [配置](#配置)
- [使用方式](#使用方式)
- [Skill 系统](#skill-系统)
- [记忆系统](#记忆系统)
- [MCP 集成](#mcp-集成)
- [项目结构](#项目结构)
- [设计决策](#设计决策)
- [License](#license)

---

## 特性

- **🧠 结构化 ReAct 循环** —— 代码驱动的 Agent 循环，事件全部类型化（`StreamText`、`ToolUseEvent`、`ToolResultEvent`、`TurnComplete`）。不靠正则解析 LLM 文本输出，每一次工具交互都是结构化 dataclass。
- **🔧 内置诊断工具** —— `read_file`（读文件）、`grep_code`（正则搜索）、`glob_files`（通配符匹配）、`run_command`（执行命令）、`edit_file`（精确替换修改）、`write_file`（写文件）、`get_environment`（环境信息采集）——Agent 探索和修复代码所需的一切。
- **📚 Skill 系统** —— 可加载的 SOP（标准操作流程），激活后注入专用 prompt 并限制可用工具。内置 Skill 包括：`parse-stack-trace`（解析调用栈）、`map-project-modules`（生成模块关系图）、`check-env-dependencies`（环境依赖检查）、`lookup-framework-docs`（查框架文档）、`apply-fix`（执行修复）。用户可自行添加 Markdown 格式的自定义 Skill。
- **🧩 MCP 集成** —— 通过 Model Context Protocol 接入外部工具。连接任意 MCP Server（Context7 文档查询、代码搜索、数据库等），其工具自动对 Agent 可用。
- **💾 持久化记忆** —— 结构化 Bug 模式记忆，包含自动提取、LLM 驱动召回和索引文件存储。Agent 会记住历史诊断，遇到相似症状时自动召回相关记忆。
- **📦 自动压缩** —— 对话上下文超过可配置的 token 阈值时，由专用压缩 LLM 自动摘要早期对话，最近几轮原文保留。
- **🔀 多模型架构** —— 三个独立可配的 LLM 后端：主模型负责深度诊断，轻量模型负责记忆筛选召回，可选模型负责上下文压缩。各自可使用不同的厂商、模型和 API Key。
- **💬 会话持久化** —— 保存、列出、恢复诊断会话。每个会话以 JSONL 格式存储，附带元信息文件和 compact 边界记录。
- **🗺️ 项目模块图** —— 可选的 `module-map.md` 描述模块间关系，Agent 诊断前先读图定位，避免重复全库 glob。
- **🎨 分层设计** —— 清晰的依赖层级：`llm/` → `conversation/` → `tools/` → `agent/` → `chat/`。下层对上层一无所知，切换 LLM 厂商只需改一个文件。

---

## 架构概览

```
┌──────────────────────────────────────────────────┐
│                  chat / app.py                    │
│         终端交互、事件编排、主循环                  │
├──────────────────────────────────────────────────┤
│                 agent / loop.py                   │
│      ReAct 循环：思考 → 行动 → 观察               │
│      自动压缩、Skill 感知的工具过滤                 │
├──────────────────┬───────────────┬───────────────┤
│   tools/         │   skills/     │    mcp/       │
│   内置工具        │   SOP 系统    │   MCP 客户端   │
│   (读、搜、改、   │   (解析、     │   (连接、      │
│    执行)          │    激活)      │    包装工具)   │
├──────────────────┴───────────────┴───────────────┤
│              conversation / manager.py            │
│       消息列表、token 估算、对话历史管理            │
├──────────────────────────────────────────────────┤
│                  llm / client.py                  │
│     OpenAI 兼容流式调用、StreamEvent 事件流        │
│     厂商无关 —— 只有这一层接触外部 API             │
└──────────────────────────────────────────────────┘

横向支撑系统（跨层使用）：
  memory/   —— Bug 模式记忆存储、召回、会话管理
  context/  —— 自动压缩：上下文溢出时摘要旧轮次
  prompts/  —— System prompt 构建（身份、规则、环境）
  config.py —— YAML 配置 + 级联合并 + 环境变量
```

### ReAct 循环流程

```
用户提交 Bug 报告
       │
       ▼
  ┌─────────────┐
  │  检索相关    │  ← 查记忆库有无相似历史 Bug
  │  记忆        │
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │  检查是否需要│  ← 上下文超阈值则压缩旧轮次
  │  自动压缩    │
  └──────┬──────┘
         ▼
  ┌─────────────┐     ┌──────────────────┐
  │  LLM 思考    │────▶│  无工具调用？     │──▶ 结束 → 输出诊断
  │  (流式输出)   │     │  任务完成         │
  └──────┬──────┘     └──────────────────┘
         │ 有工具调用
         ▼
  ┌─────────────┐
  │  执行工具    │  ← Skill 感知：仅允许的工具可执行
  └──────┬──────┘
         │ 观察结果
         ▼
  ┌─────────────┐
  │  结果写回    │  ← 工具输出写回对话历史
  │  对话历史    │
  └──────┬──────┘
         │
         └─────── 循环（最多 max_iterations 轮）──────┘
```

---

## 快速开始

### 环境要求

- Python 3.11+
- 一个 OpenAI 兼容的 LLM API Key（DeepSeek、GLM、Qwen、OpenAI 等均可）

### 1. 克隆并安装

```bash
git clone <仓库地址> bugdoctor
cd bugdoctor

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装（含全部依赖）
pip install -e .
```

安装后可直接使用 `bugdoctor` 命令。

### 2. 配置

复制示例配置并填入你的 API Key：

```bash
cp bugdoctor/config.example.yaml bugdoctor/config.local.yaml
```

编辑 `bugdoctor/config.local.yaml`：

```yaml
llm:
  provider: openai-compat
  model: deepseek-v4-pro        # 或其他：gpt-4o, claude-sonnet-5 等
  api_key: "sk-你的密钥"         # 也可设环境变量 BUGDOCTOR_API_KEY
  base_url: https://api.deepseek.com
  max_output_tokens: 4096
```

> 也支持环境变量：`BUGDOCTOR_API_KEY`、`BUGDOCTOR_MODEL`、`BUGDOCTOR_BASE_URL`

### 3. 运行

```bash
# 诊断当前目录下的项目
bugdoctor

# 也可用 python -m bugdoctor

# 诊断指定项目
bugdoctor --project /path/to/your/project

# 开启全新会话
bugdoctor --new

# 恢复之前的会话
bugdoctor --session 20260705_093004_uwss
```

粘贴报错信息或描述 Bug，按两次回车发送。Agent 会自动探索代码、诊断问题并输出结论。

输入 `quit` 或 `exit` 退出。

---

## 安装

### 依赖

| 包 | 版本 | 用途 |
|---|---|---|
| `openai` | ≥ 1.40.0 | LLM API 客户端（OpenAI 兼容流式调用） |
| `pydantic` | ≥ 2.0 | 工具参数校验、JSON Schema 生成 |
| `pyyaml` | ≥ 6.0 | 配置文件解析、Skill 前言、记忆元数据 |
| `httpx` | ≥ 0.27 | MCP HTTP 传输 |
| `mcp` | ≥ 1.0 | Model Context Protocol 客户端库 |
| `colorama` | — | 终端彩色输出 |

一键安装：

```bash
pip install -e .
```

---

## 配置

BugDoctor 采用**级联合并**的配置方式：后加载的文件覆盖先加载的。

| 优先级 | 路径 | 用途 |
|--------|------|------|
| 1（最底层） | `bugdoctor/config.yaml` | 包内默认配置 |
| 2 | `bugdoctor/config.local.yaml` | 本地密钥（gitignored） |
| 3 | `.bugdoctor/config.yaml` | 应用级覆盖（Skill、MCP） |
| 4 | `.bugdoctor/config.local.yaml` | 应用级密钥 |
| 5 | `<项目>/.bugdoctor/config.yaml` | 按项目覆盖 |
| 6（最高） | `<项目>/.bugdoctor/config.local.yaml` | 按项目密钥 |

### 完整配置参考

```yaml
# ── 主诊断 LLM（必填）────────────────────────────
llm:
  provider: openai-compat          # 目前支持的唯一 provider
  model: deepseek-v4-pro           # 任意 OpenAI 兼容模型
  api_key: ""                      # 或环境变量 BUGDOCTOR_API_KEY
  base_url: https://api.deepseek.com
  max_output_tokens: 4096

# ── 记忆召回 LLM（可选）──────────────────────────
# 用于筛选相关记忆的轻量模型，不配则回退到 llm
recall_llm:
  provider: openai-compat
  model: glm-5.2
  api_key: ""                      # 或环境变量 BUGDOCTOR_RECALL_API_KEY
  base_url: https://你的实例地址.maas.aliyuncs.com/compatible-mode/v1
  max_output_tokens: 512

# ── 上下文压缩 LLM（可选）────────────────────────
# 用于超阈值时的对话摘要，不配则回退到 recall_llm，再回退到 llm
compact_llm:
  provider: openai-compat
  model: qwen3.7-plus
  api_key: ""                      # 或环境变量 BUGDOCTOR_COMPACT_API_KEY
  base_url: https://你的实例地址.maas.aliyuncs.com/compatible-mode/v1
  max_output_tokens: 2048

# ── Agent 设置 ──────────────────────────────────
compact_threshold: 8000            # 触发自动压缩的 token 阈值
max_agent_iterations: 30           # 单轮 ReAct 最大迭代次数

# ── MCP Server（可选，建议写在 .bugdoctor/config.yaml）─
mcp_servers:
  - name: context7
    command: npx
    args: ["-y", "@upstash/context7-mcp"]
    env:
      CONTEXT7_API_KEY: "你的密钥"
  # 或 HTTP 方式：
  # - name: my-server
  #   url: https://mcp.example.com/mcp
  #   headers:
  #     Authorization: "Bearer token"
```

---

## 使用方式

### 交互模式

```bash
bugdoctor --project ./my-app
```

```
BugDoctor — model: deepseek-v4-pro
Recall model: glm-5.2
Workspace: /home/user/my-app
Session: 20260705_120000_abc1  |  数据: .../.bugdoctor
Skills: 5  (dir: .../.bugdoctor/skills)
Tools: read_file, glob_files, grep_code, run_command, get_environment,
       write_file, edit_file, load_skill
粘贴错误信息或描述 bug，空行发送，输入 quit 退出。

you> Traceback (most recent call last):
  File "main.py", line 42, in process
    result = data['key'] / divisor
KeyError: 'key'
```

Agent 会依次：
1. 检索记忆库中是否有相似的历史 Bug
2. 阅读相关源码文件
3. 提出并验证假设
4. 输出根因结论和修复建议

### 命令行参数

```
usage: bugdoctor [-h] [--project PATH] [--config PATH]
                 [--new] [--session ID]

options:
  --project PATH    目标项目目录（默认当前目录）
  --config PATH     自定义 config.yaml 路径
  --new             创建全新会话（跳过会话选择器）
  --session ID      按 ID 恢复指定会话
```

### 会话管理

BugDoctor 自动保存诊断会话。不带 `--new` 或 `--session` 启动时，会显示会话选择器：

```
发现 2 个历史对话:
  1. 20260705_093004_uwss — 8 条消息 — 2026-07-05 09:30
  2. 20260705_120000_abc1 — 14 条消息 — 2026-07-05 12:00
选择 (1-2, n=新建, q=退出):
```

会话以 JSONL 格式存储在 `.bugdoctor/sessions/`，配有 `.meta.json` 元信息文件。

---

## Skill 系统

Skill 是用 Markdown + YAML 前言定义的可加载 SOP。Agent 诊断过程中可动态激活 Skill，切换专用模式并限制可用工具。

### 内置 Skill

| Skill | 说明 |
|-------|------|
| `parse-stack-trace` | 解析 Python/Java 调用栈，定位异常抛出点 |
| `map-project-modules` | 生成或刷新项目模块关系图 |
| `check-env-dependencies` | 检查 Python 环境和依赖问题 |
| `lookup-framework-docs` | 查框架/库文档（需配 Context7 MCP） |
| `apply-fix` | 将诊断结论落地为代码修改 |

### Skill 文件格式

Skill 放在 `.bugdoctor/skills/*.md`：

```markdown
---
name: my-custom-skill
description: 分析数据库查询性能问题
allowedTools:
  - read_file
  - grep_code
  - run_command
---

## SOP：数据库查询分析

1. 读取数据库配置文件
2. 用 grep 找出慢查询模式
3. ...
```

- `name` —— 纯小写 + 连字符，用作 `load_skill` 的参数
- `description` —— 显示在 Skill 目录中
- `allowedTools` —— 该 Skill 激活时限制 LLM 可调用的工具（`load_skill` 始终可用）
- 正文 —— 激活后合并进 system prompt

Agent 在对话中可通过调用 `load_skill` 工具动态激活 Skill。Skill 文件支持热加载（每次访问时重新解析）。

---

## 记忆系统

BugDoctor 维护一个持久化的 Bug 模式记忆库，随使用不断增长。

### 工作原理

1. **提取** —— 每轮诊断结束后，将诊断结论发送给 LLM 判断：create（新建记忆）、update（更新已有）、delete（删除过时）、或 skip（跳过）。
2. **存储** —— 记忆以 Markdown 文件存储在 `.bugdoctor/memory/`，包含 YAML 前言（症状、关键符号、根因、修复方向）。索引文件 `MEMORY.md` 汇总所有记忆。
3. **召回** —— 每次诊断前，专用召回 LLM 扫描记忆索引并与用户报错比对，选出最多 3 条最相关记忆，以 `<system-reminder>` 注入给诊断 Agent。
4. **注入提示** —— 召回的记忆附有提醒头，要求 Agent 以当前代码为准进行验证，而非盲目套用旧结论。

### 记忆文件示例

```markdown
---
name: isdigit-negative-amount
description: isdigit() 无法识别负数，amount 保持为字符串导致乘法 TypeError
metadata:
  type: bug_pattern
  symptoms: [TypeError, multiply sequence, float]
  key_symbols: [_normalize_amount, Transaction.amount]
  root_cause: str.isdigit() 对 '-5' 返回 False，amount 未转为 int
  fix_approach: 用 int/float 解析替代 isdigit()
---

## 症状

运行 main.py 报 TypeError: can't multiply sequence by non-int of type 'float'

## 诊断过程

CSV 中 Amount=-5，_normalize_amount 因 isdigit 失败返回字符串，
report_service 乘法时报错。

## 修复方向

在 transaction_repository._normalize_amount 中用 try/except 转 int/float
```

---

## MCP 集成

BugDoctor 可连接 MCP（Model Context Protocol）Server，将其工具接入 Agent。

### 配置方式

在 `.bugdoctor/config.yaml` 中添加 MCP Server：

```yaml
mcp_servers:
  # 本地进程方式（stdio）
  - name: context7
    command: npx
    args: ["-y", "@upstash/context7-mcp"]
    env:
      CONTEXT7_API_KEY: "${CONTEXT7_API_KEY}"

  # 远程 HTTP 方式
  - name: custom-docs
    url: https://mcp.docs.example.com/mcp
    headers:
      Authorization: "Bearer ${TOKEN}"
```

### 工具命名规则

MCP 工具以 `mcp_<server>_<tool>` 格式注册。例如 Context7 的 `resolve-library-id` 工具在 Agent 中名为 `mcp_context7_resolve_library_id`。Skill 的 `allowedTools` 中写 `mcp_context7_*` 即可批量放行。

---

## 项目结构

```
bugdoctor/
├── __init__.py              # 包标记
├── __main__.py              # python -m bugdoctor 入口（argparse + asyncio）
├── app.py                   # 主程序：终端 I/O、事件编排、会话管理
├── config.py                # AppConfig、LLMConfig、YAML 级联加载器
├── project_map.py           # 可选 module-map.md 发现与提示注入
│
├── llm/                     # 第 1 层：LLM API 抽象
│   ├── client.py            #   OpenAICompatClient —— 流式调用、tool call 解析
│   ├── events.py            #   StreamEvent 联合类型：TextDelta | ToolCall* | StreamEnd
│   └── serializer.py        #   内部 Message → OpenAI API 格式（唯一知道 API 格式的文件）
│
├── conversation/            # 第 2 层：对话状态管理
│   ├── models.py            #   Message、ToolUseBlock、ToolResultBlock、token 估算
│   └── manager.py           #   ConversationManager：历史增删、system-reminder 注入
│
├── tools/                   # 第 3 层：工具注册表与内置工具
│   ├── base.py              #   Tool 基类、ToolRegistry（含 pydantic 校验）
│   ├── factory.py           #   create_registry() —— 注册全部工具
│   ├── read_file.py         #   读文件（支持行号、起止行）
│   ├── glob_files.py        #   通配符匹配文件
│   ├── grep_code.py         #   正则搜索代码
│   ├── run_command.py       #   执行 shell 命令（超时 + 沙箱）
│   ├── edit_file.py         #   精确字符串替换（需先 read_file）
│   ├── write_file.py        #   写文件（仅限 .bugdoctor/ 下）
│   ├── get_environment.py   #   采集 Python 版本、OS、依赖包信息
│   ├── load_skill.py        #   动态激活 Skill
│   ├── read_tracker.py      #   跟踪已读文件（防止未读就改）
│   └── sandbox.py           #   路径安全：解析、跳过目录、项目内限制
│
├── agent/                   # 第 4 层：ReAct 循环
│   └── loop.py              #   Agent.run() —— 结构化 ReAct 引擎
│
├── skills/                  # Skill 系统
│   ├── parser.py            #   YAML 前言 + Markdown 正文 → SkillDef
│   ├── loader.py            #   扫描 .bugdoctor/skills/，支持热加载
│   ├── manager.py           #   激活、工具过滤、SOP 注入
│   ├── paths.py             #   Skill 目录解析
│   └── defaults/            #   内置 Skill 定义
│
├── memory/                  # Bug 模式记忆
│   ├── store.py             #   MemoryStore：CRUD、LLM 自动提取
│   ├── recall.py            #   LLM 驱动记忆筛选与注入
│   ├── replay.py            #   恢复会话的回放展示
│   └── session.py           #   SessionStore：JSONL 持久化、会话选择器
│
├── mcp/                     # Model Context Protocol
│   ├── client.py            #   MCPClient：stdio / HTTP 传输、工具列举与调用
│   ├── tool_wrapper.py      #   将 MCP 工具包装为 BugDoctor Tool
│   └── manager.py           #   MCPManager：连接所有 Server、注册工具
│
├── context/                 # 上下文管理
│   └── compact.py           #   自动压缩：token 阈值 → LLM 摘要 → 重建历史
│
├── prompts/                 # System prompt 构建
│   ├── system.py            #   build_system_prompt() 公开接口
│   ├── builder.py           #   PromptBuilder：按优先级排序的 section 拼装
│   └── sections.py          #   预定义 section：身份、诊断规则、输出风格、环境信息
│
├── config.yaml              # 默认配置（已提交）
├── config.local.yaml        # 本地密钥（gitignored）
└── config.example.yaml      # 带注释的配置模板
```

### 应用数据目录

```
.bugdoctor/
├── config.yaml              # 应用级配置（MCP Server 等）
├── config.local.yaml        # 应用级密钥
├── skills/                  # 用户自定义 Skill
│   ├── parse-stack-trace.md
│   ├── map-project-modules.md
│   ├── check-env-dependencies.md
│   ├── lookup-framework-docs.md
│   └── apply-fix.md
├── memory/                  # 持久化 Bug 模式记忆
│   ├── MEMORY.md            # 记忆索引
│   └── *.md                 # 单条记忆文件
├── sessions/                # 会话持久化
│   ├── *.jsonl              # 消息历史
│   └── *.meta.json          # 会话元信息
└── module-map.md            # 可选：项目模块关系图
```

---

## 设计决策

### 为什么用结构化 ReAct 而非 Prompt 级 ReAct？

Prompt 级 ReAct 靠正则从 LLM 文本输出中提取动作。这种方式很脆弱——LLM 可能改变工具调用格式、幻觉出工具名、或在思考文本中嵌套动作。

结构化 ReAct 使用**原生 tool calling**：LLM 在 API 响应中返回结构化的 `tool_calls` JSON，Agent 循环将其作为类型化事件分发。循环本身只是一个 `for _ in range(max_iterations)` —— 流式接收 LLM 输出，收集工具调用，执行，把结果写回。没有正则、没有解析歧义。

### 为什么用厂商无关的 Message 模型？

`conversation/models.py` 中的 `Message` 有意与任何 LLM 厂商格式解耦。它只有 `role`、`content`、`tool_uses: list[ToolUseBlock]`、`tool_results: list[ToolResultBlock]`。这意味着：

- ReAct 循环、记忆系统、Skill 管理器都操作同一数据结构
- 从 DeepSeek 切到 OpenAI 再切到 Anthropic，只需改 `serializer.py` 一个文件
- 工具执行层不需要知道是哪个 LLM 发起的调用

### 为什么不同任务用不同 LLM？

- **主模型**（如 DeepSeek V4）—— 承担重量级诊断推理
- **召回模型**（如 GLM-5.2，512 tokens）—— 轻量记忆筛选，每轮诊断前运行
- **压缩模型**（如 Qwen 3.7+，2048 tokens）—— 上下文摘要，仅在超阈值时运行

分离关注点、控制成本：不会在机械任务（"5 条记忆中哪条相关？"）上消耗昂贵的推理 token。

### 为什么用文件存储记忆而非向量数据库？

记忆系统刻意使用 Markdown + YAML 前言 + 平面目录 + `MEMORY.md` 索引。理由：

1. **透明** —— 记忆是人类可读、可编辑的纯文本
2. **可移植** —— 不依赖数据库，可通过 git 跨机器同步
3. **LLM 原生** —— 召回 LLM 直接阅读索引并选择文件，不需 embedding 模型
4. **课程约束** —— 展示结构化提取 + 召回能力，而非依赖向量数据库黑盒

### 为什么用 chars/3.5 估算 token 而非精确计数？

精确计 token 需要对 tokenizer 的额外 API 调用。`chars/3.5` 启发式算法免费、即时、对压缩决策足够准确（误差 ±10-15%）。自动压缩阈值是软触发而非硬限制，精度不是关键。

### 配置级联合并

配置从 6 个路径逐层覆盖（包内 → 应用 → 项目，每层有 `.yaml` → `.local.yaml`）。好处：

- 全局设 API Key → `bugdoctor/config.local.yaml`（gitignored）
- 按项目配 MCP Server → `<项目>/.bugdoctor/config.yaml`
- 不同项目不同阈值 → 不用动全局配置

---

## License

MIT
