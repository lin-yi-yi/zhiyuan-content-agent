# AI 内容增长 Agent v0.5

**普通人的AI提效实验室内容工作台**

半自动内容生产 Agent —— 帮普通人把 AI 工作流、Agent 开发、开源项目和提效案例，转化成适合小红书/抖音图文发布的内容资产。

当前已推进到 v0.5：除 URL/GitHub 导入外，已支持 Agent 工作台、自定义选题创作、入库前编辑、重复提示、素材库一源多题、发布方案生成、组合诊断、卡片实时预览与样式参数化、质量评分、人工审核清单、ZIP 发布包导出、异步 Agent Run、失败重试、自动轻量改稿、Agent 决策摘要、RAG 知识库检索、受控工具调用白名单、发布前预测、发布后指标录入和复盘校准。

## 项目交接文档

给后续 Claude Code、Codex 或同事接手开发时，请先阅读：

```text
README.md
docs/v0.4-agent-architecture.md
docs/v0.5-data-loop.md
```

## 第一版功能范围

- Agent 工作台：一句话目标自动串起选题、发布包、卡片、质量评分和下一步建议
- Agent 执行记录：记录每次 Agent Run 的目标、步骤、状态、结果和失败重试
- 选题池：创建、评分、筛选选题
- 自定义选题创作：AI 自动调研 / 主题灵感创作，支持候选入库前编辑和重复提示
- 素材库：查看 source 列表、类型筛选、详情、已生成选题数、质量提示，并可从同一素材生成多个选题角度
- 图文发布包生成：AI 生成标题、正文、卡片
- 发布方案生成器：选择标题、封面、正文版本后生成新方案，并用组合诊断提示是否需要重新生成卡片
- 卡片编辑器：预览和修改每页卡片，支持单卡实时预览、字号/密度/强调块/页脚参数化、PNG / ZIP 导出
- 人工审核：合规检查、事实核验、发布前 checklist
- 质量评分：评估发布包钩子、收藏价值、文字密度和合规风险
- 发布前预测：记录预计浏览、点赞、收藏、评论、涨粉、置信度、判断理由和不确定因素
- 发布数据录入：手动记录平台真实数据，自动沉淀到发布记录
- 7天复盘：对比预测与真实表现，校准选题、标题、封面和模板判断

## 第一版不做什么

- 不自动发布到任何平台
- 不做多账号管理
- 不做全平台适配
- 不做视频剪辑
- 不做完全自动选题发布闭环

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 18 + TypeScript + Vite |
| 后端 | Python FastAPI |
| 数据库 | MySQL + SQLAlchemy 2.x |
| 模型 | local 规则模型 + DeepSeek / 千问 / 豆包 / Kimi (OpenAI-compatible adapter) |

## 本地启动

### 1. 前置条件

- Python 3.11+
- Node.js 18+
- MySQL 8.0+

### 2. 创建数据库

```sql
CREATE DATABASE ai_content_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 3. 配置环境变量

复制 `.env` 文件，填入你的配置：

```bash
DATABASE_URL=mysql+pymysql://root:password@127.0.0.1:3306/ai_content_agent?charset=utf8mb4

# 默认可用，无需 API Key
DEFAULT_LLM_PROVIDER=local

# 如需调用真实模型，再配置对应 Key
DEEPSEEK_API_KEY=sk-xxx

# 可选：为不同 Agent 任务配置不同模型
TOPIC_SCORE_PROVIDER=deepseek
DRAFT_GENERATION_PROVIDER=qwen
CARD_GENERATION_PROVIDER=doubao
COMPLIANCE_CHECK_PROVIDER=deepseek
```

### 4. 启动后端

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 5. 启动前端

```bash
cd frontend
npm install
npx vite --host 127.0.0.1 --port 5173
```

如果后端不是 8000 端口，可以指定代理目标：

```bash
VITE_API_TARGET=http://127.0.0.1:8001 npm run dev -- --host 127.0.0.1 --port 5176
```

### 6. 打开浏览器

http://localhost:5173

## 如何新增选题

1. 打开工作台 → 选题池
2. 点击「+ 新增选题」
3. 填写标题、来源类型、来源网址、原始摘要
4. 点击「创建选题」
5. 点击「评分」获取选题评分
6. 点击「生成」生成小红书图文发布包

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|:--:|
| Phase 1 | 项目骨架 + MySQL + 选题池 CRUD | ✅ 完成 |
| Phase 2 | MySQL 全表 + Drafts/Cards CRUD | ✅ 完成 |
| Phase 3 | LLM Router + 多模型切换 | ✅ 完成 |
| Phase 4 | 选题评分 Agent | ✅ 本地规则版完成，可接真实 LLM |
| Phase 5 | 发布包和卡片生成 | ✅ 本地规则版完成，可编辑、可导出 PNG |
| Phase 6 | 数据录入和7天复盘 | ✅ 规则聚合版完成 |
| v0.2-A | 真实模型 JSON 稳定链路 | ✅ 已加入 JSON 抽取、修复重试、response_format 回退 |
| v0.2-B | 草稿版本管理 | ✅ 可查看同一选题多个发布包，可删除草稿 |
| v0.2-C | 生成结果 schema 兜底 | ✅ 已补齐缺字段、卡片不足、分数越界等情况 |
| v0.2-D | PNG 卡片视觉升级 | ✅ 已优化模板感、长文本适配和导出排版 |
| v0.2-E | URL / GitHub 信源导入 | ✅ 可粘贴 URL 自动生成选题，GitHub 优先读取 README |
| v0.2-F | 导入预览与编辑确认 | ✅ 先预览标题/摘要/来源类型，确认后再入库 |
| v0.2-G | 导入后选题建议 | ✅ 预览时生成多个内容角度，可点选后入库 |
| v0.2-H | 选中建议后自动评分 | ✅ 确认导入时可勾选自动评分，评分写入选题 |
| v0.2-I | 小红书卡片风格升级 | ✅ 新增卡面结构、主题/版式选项、响应式预览，并同步 PNG 导出样式 |
| v0.2-J | 发布方案生成器 | ✅ 可选标题/封面/正文版本生成新方案，支持指定页数、组件化卡片、旧版本保留 |
| v0.2-K | 发布包质量评分 | ✅ 可对 draft + cards 做 9 维评分，local 规则模型可兜底 |
| v0.2-L | 人工审核清单 | ✅ 每个发布包版本可勾选发布前审核项、填写备注并保存状态 |
| v0.2-M | 卡片 ZIP 导出包 | ✅ 一次导出多张 PNG + 标题/正文/标签/评论引导文案 |
| v0.2-N | 自定义选题创作 | ✅ 支持 AI 自动调研 / 主题灵感创作、快速/深度模式、local/豆包/DeepSeek provider |
| v0.2-O | 选题素材库基础版 | ✅ 可查看 source 列表、来源类型统计和每个素材已生成选题数 |
| v0.3-A | 内容增长 Agent 执行系统 | ✅ Agent 工作台 + agent_runs/agent_steps + 一句话生成完整发布包 |
| v0.3-B | Agent 异步执行与自动改稿 | ✅ 创建后立即返回、后台执行、前端轮询、失败重试、低分自动轻量修订一次 |
| v0.3-C | Agent 决策摘要 | ✅ 新增 agent_decision 步骤，输出选题理由、质量门槛、改稿状态、人工复查重点和下一步动作 |
| v0.3-D | 素材库一源多题 | ✅ 素材详情页可生成 5 个不同选题角度、入库前编辑、去重提示、确认后关联原 source |
| v0.3-E | 自定义选题入库前编辑 + 去重增强 | ✅ 自定义创作候选可编辑后入库，返回 duplicate_hint，素材列表/详情显示质量与重复提示 |
| v0.3-F | 发布包组合诊断 | ✅ 发布包编辑页根据标题/封面/正文/标签/页数/模板实时评分，提示问题和是否应生成匹配卡片 |
| v0.3-G | 卡片编辑器样式参数化与实时预览 | ✅ 单卡编辑 Modal 支持右侧实时预览，字号/密度/强调块/页脚参数写入 style_json，并同步 PNG/ZIP 导出 |
| v0.3-H | 数据闭环增强基础版 | ✅ 复盘加入收藏率/点赞率/评论率/关注转化率，支持按角度/内容类型/模板聚合 |
| v0.4-A | Agent 架构边界与 RAG 基础层 | ✅ 新增 workspace / knowledge_base 数据隔离、能力白名单、LangChain 可选切分器、RAG 索引/检索/拒答 API、架构边界页面 |
| v0.4-B | RAG 接入 Agent 工作台 | ✅ 素材库显示索引状态，Agent Run 新增可选 retrieve_context 步骤，工作台可选择知识库并展示检索证据 |
| v0.4-C | 本地混合检索与 RAG 实验台 | ✅ knowledge_chunks 写入本地 128 维哈希 embedding，检索改为词面+向量混合评分，新增 RAG 实验页和 smoke 脚本 |
| v0.4-D | 受控 Function Calling 工具层 | ✅ 新增 `rag.search` / `rag.answer` / `source.index` 工具白名单、工具执行 API 和架构页工具边界展示 |
| v0.5-A | 发布前预测和复盘校准 | ✅ 新增 content_predictions、预测 API、发布记录自动绑定、真实指标录入后标记 settled、复盘生成预测校准摘要 |
| v0.5-B | 桌面端 Apple 风格 UI 收口 | ✅ 全局浅色系统界面、半透明面板、Apple 蓝主操作、细边框和轻阴影，保持桌面工作台信息密度 |

## v0.4 Agent 架构边界

详细设计见：

```text
docs/v0.4-agent-architecture.md
```

核心约束：

- RAG 数据单独进入 `workspaces` / `knowledge_bases` / `knowledge_documents` / `knowledge_chunks`
- 检索和回答必须限制在当前 `workspace_id` + `knowledge_base_id`
- 只索引已入库素材，不读取本机任意文件、浏览器状态、账号密码或外部密钥
- 证据不足时 RAG 必须拒答
- 当前仍保留 v0.3 线性 Agent 流程，LangGraph 作为后续分支工作流入口
- Agent 工作台启用知识库检索后，会先执行 `retrieve_context`，再进入选题/发布包生成
- v0.4-C 当前是本地哈希 embedding + 混合检索，适合离线开发验证；外部 embedding/vector DB 是后续升级项
- v0.4-D 工具调用只能执行后端白名单里的 `rag.search`、`rag.answer`、`source.index`，不接受任意函数名、SQL、文件路径或外部账号能力

## v0.5 数据闭环

详细设计见：

```text
docs/v0.5-data-loop.md
```

核心约束：

- 发布前预测是人工/半自动判断记录，不自动发布、不读取平台账号、不绕过平台规则
- 预测按 `draft_id`、`publish_log_id`、`platform` 绑定，避免不同平台或不同发布批次混淆
- 创建发布记录时自动绑定同平台、同发布包的最新未绑定预测
- 录入真实指标后，关联预测状态进入 `settled`
- 周期复盘会计算浏览预测误差、整体高估/低估方向、误差最大的内容，并写入 `prediction_calibration`
- 当前 v0.5 仍以手动预测为主，下一步才基于历史数据给出自动预测参考

## 本地验证

发布包生成链路的冒烟测试：

```bash
python3 scripts/smoke_publish_generation.py
```

脚本会打开本地前端，选择一个已生成选题，点击「生成匹配卡片」，检查页面不白屏、卡片正常渲染、控制台无错误，并删除临时生成的发布包。

v0.4 RAG Agent 链路冒烟测试：

```bash
python3 scripts/smoke_v04_rag_agent.py
```

脚本会创建临时素材、索引知识库、启动启用 RAG 的 Agent Run、检查 `retrieve_context`、验证证据不足拒答，并清理临时数据。
同时会检查 v0.4 工具白名单和 `rag.search` 工具执行入口。

自定义选题创作建议用 local provider 验证，避免产生真实模型费用：

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"mode":"inspiration","research_depth":"quick","theme":"普通人怎么用 AI 自动化副业内容","target_audience":"AI 新手 / 自媒体人","viewpoint":"先跑通半自动流程，再谈全自动","content_type":"tutorial","source_urls":[],"provider":"local"}' \
  http://127.0.0.1:8001/api/topics/custom-ideas
```

Agent 工作台链路建议用 local provider 验证：

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"goal":"普通人怎么用 AI 自动化副业内容","mode":"inspiration","research_depth":"quick","target_audience":"AI 新手 / 自媒体人","viewpoint":"先跑通半自动流程，再谈全自动","provider":"local","model":"local-rule-based-v0","auto_score":true}' \
  http://127.0.0.1:8001/api/agent-runs
```

接口会先返回 `pending` 状态，后台继续执行；轮询 `GET /api/agent-runs/{id}` 可看到 10 个步骤和 `result_json.agent_decision`。

发布包组合诊断验证：

```bash
python3 -m compileall backend/app

cd frontend
npm run build
```

v0.5 数据闭环验证：

```bash
python3 -m compileall backend/app

cd frontend
npm run build
```

浏览器打开 `http://127.0.0.1:5173/`，进入「数据录入」应看到「发布前预测」面板；进入「7天复盘」应看到「预测校准」面板。

浏览器打开 `http://127.0.0.1:5178/`，进入「发布包编辑」，选择已有选题后应看到「组合诊断」面板；切换模板后，面板应提示组合已修改并建议点击「生成匹配卡片」。点击任意卡片进入编辑 Modal，应看到右侧实时预览；修改标题、字号、密度、强调块或页脚开关后，预览应即时变化，保存后卡片列表同步更新。

## API 端点

### 健康检查
```
GET /health
GET /api/health
```

### Agent 工作台
```
POST   /api/agent-runs                 启动一次内容增长 Agent 任务
GET    /api/agent-runs                 Agent 执行记录列表
GET    /api/agent-runs/{id}            Agent 执行详情、步骤和产物
POST   /api/agent-runs/{id}/retry      从失败步骤继续重试
```

`POST /api/agent-runs` 可选 RAG 参数：

```json
{
  "use_rag": true,
  "knowledge_base_id": 1,
  "rag_top_k": 5
}
```

启用后会把检索结果写入 `result_json.rag_context`，工作台会展示证据状态和命中的 chunk。

### v0.4 RAG / Tool Calling
```
GET    /api/v04/architecture            架构边界、能力白名单、工具白名单
GET    /api/v04/workspaces              工作区列表
GET    /api/v04/knowledge-bases         知识库列表
POST   /api/v04/knowledge-bases         新增知识库
POST   /api/v04/rag/index-source        把已有 source 索引进知识库
POST   /api/v04/rag/search              在知识库内检索 evidence chunks
POST   /api/v04/rag/answer              基于 evidence 回答或拒答
GET    /api/v04/tools                   工具白名单
POST   /api/v04/tools/execute           执行白名单工具
```

`POST /api/v04/tools/execute` 示例：

```json
{
  "tool_name": "rag.search",
  "knowledge_base_id": 1,
  "arguments": {
    "query": "LangChain、RAG 和 LangGraph 的边界是什么？",
    "top_k": 5
  }
}
```

### 选题池
```
GET    /api/topics                      列表（支持 ?status= & ?page= & ?limit=）
POST   /api/topics                      新增
GET    /api/topics/{id}                 详情
PUT    /api/topics/{id}                 更新
DELETE /api/topics/{id}                 删除
POST   /api/topics/import-url           从 URL / GitHub 导入选题
POST   /api/topics/import-url/preview   预览 URL / GitHub 导入结果
POST   /api/topics/import-url/confirm   确认预览内容并创建选题
POST   /api/topics/custom-ideas         自定义选题创作，生成 5 个选题建议
POST   /api/topics/custom-ideas/confirm 确认自定义选题建议并入库
POST   /api/topics/{id}/score           评分（Phase 4）
POST   /api/topics/{id}/generate-draft  生成发布包（Phase 5）
```

### 素材库
```
GET    /api/sources                     素材列表
GET    /api/sources/stats               素材统计
GET    /api/sources/{id}                素材详情及关联选题
POST   /api/sources/{id}/topic-ideas    从一个素材生成 3-5 个选题建议
POST   /api/sources/{id}/topic-ideas/confirm 确认素材候选并入库
```

### 发布包和卡片
```
GET    /api/drafts/{id}                 草稿详情
GET    /api/drafts?topic_id=            草稿列表
GET    /api/drafts/topic/{topic_id}/latest 选题最新草稿
PUT    /api/drafts/{id}                 更新草稿
DELETE /api/drafts/{id}                 删除草稿
POST   /api/drafts/{id}/generate-variant 根据标题/封面/正文版本生成新发布方案
POST   /api/drafts/{id}/evaluate        发布包质量评分
GET    /api/drafts/{id}/review-checklist 获取发布前审核清单
PUT    /api/drafts/{id}/review-checklist 保存发布前审核清单
GET    /api/cards/draft/{draft_id}      获取卡片列表
PUT    /api/cards/{id}                  更新卡片
```

### 发布和数据
```
POST   /api/publish-logs                创建发布记录
GET    /api/publish-logs                发布记录列表
POST   /api/publish-logs/{id}/metrics   录入数据指标
GET    /api/publish-logs/{id}/metrics   查看数据指标
GET    /api/predictions                 发布前预测列表（支持 ?draft_id= & ?publish_log_id=）
POST   /api/predictions                 创建发布前预测
GET    /api/predictions/{id}            预测详情
PUT    /api/predictions/{id}            更新预测
POST   /api/predictions/{id}/attach-log 绑定预测到发布记录
```

### 7天复盘
```
POST   /api/reports/weekly              生成复盘
GET    /api/reports/weekly              复盘列表
GET    /api/reports/weekly/{id}         复盘详情
```

### 模型管理
```
GET    /api/models/providers            可用模型列表
GET    /api/models/task-defaults        Agent 任务默认模型
POST   /api/models/test/{provider}      测试连接
POST   /api/models/chat                 调用模型对话
GET    /api/models/runs                 调用记录
```
