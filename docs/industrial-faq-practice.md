# 工业产品 FAQ：本人运行、解释和排错练习

本练习使用仓库内人工编写的合成工业资料、词项检索和本地规则摘录，不调用在线模型，不等于真实产品参数或客户验收。真实授权资料尚未纳入固定集。先完成下面的操作，再按本人实际完成的程度描述能力。

## 先运行

在项目根目录执行（需要已安装的 Python 3.12 环境和 Node.js 22）：

```bash
cd /path/to/ai-content-agent
(cd frontend && npm run build)
.venv/bin/python scripts/evaluate_industrial_faq.py --help
.venv/bin/python scripts/evaluate_industrial_faq.py --serve --port 8772
```

脚本从新建的临时数据库开始执行导入、核验、入库、检索、生成、修改和重审，打印检查结果后保留本机页面。页面地址和任务编号以脚本输出为准；Ctrl+C 结束并清理临时数据。不要用现有 `.data` 中的个人或客户记录做坏例实验。

默认输出位于 `.data/evaluations/industrial-faq-evaluation.json` 与同名 `.delivery.md`，每次运行会更新本地报告，不覆盖仓库中的历史验收。需要保留多轮记录时追加不同的 `--output /tmp/my-industrial-faq.json`。

本地规则生成用于复现状态和引用行为，不是本地大语言模型。合成脚本结果只能支持对应样本及检查项；语义模型、在线供应商表现和客户内容质量需要分别验证。

## 按页面复现一条流程

1. 在「品牌与资料」的「更多」菜单打开核验笔记查看合成材料。说出版本、来源、授权依据、原文定位，以及每项产品参数对应哪句话。版本变更应新建笔记并撤销旧版本，不能偷偷改已核验事实。
2. 在「内容任务」选择演示品牌和「产品与服务答疑」，使用本地摘录。展开「必须有依据的产品参数」，型号填 `合成星桥 XP-24`，参数分两行填 `额定电压`、`额定流量`（与材料中的字段一致）。生成后打开「查看依据」，找到来源片段、资料版本和核验笔记。
3. 核对正文与卡片后退回一次，进入编辑器修改原稿，保存，再回任务重新送审。解释 `rejected → awaiting_review → approved`。
4. 批准后下载正式交付 Markdown，再修改正文。确认旧批准失效，任务返回待审；未经再次批准不能下载有效交付。重新审核后下载新版本，比较内容 hash、正文和出处。
5. 在核验笔记中添加同产品同参数的冲突值，观察核验被拒；先核实资料和版本，再决定撤销哪一版。系统只比较显式字段，不会自动判断哪份材料正确，也不会识别所有语义冲突。
6. 把必需参数改为资料没有的 `售价`，确认生成前停止；再阅读脚本报告里的缺证、过期、资料范围和失败重试结果。用界面补资料后再重试一次，解释失败发生在哪个步骤、为何重试没有复制已有正文。

演示资料的 `example.com` 地址是虚构定位标识，不需要打开或访问。实际试点由客户提供约定的可追溯位置与授权。

## 解释请求怎样走

从浏览器「生成内容草稿」开始，自己在编辑器依次找出：

- `frontend/src/api/client.ts` 的请求及错误处理 → `backend/app/api/routes/agent_runs.py` 的参数校验和任务接口。
- `backend/app/services/content_growth_agent.py` 的 LangGraph 节点、证据不足停止、失败重试和人工审核。
- `backend/app/agent_core/rag_service.py` 的范围过滤、切块和检索；BM25/RRF 是召回融合，不能称为交叉编码器重排。
- `backend/app/services/evidence_notes.py` 的核验与参数冲突规则 → `review_lifecycle.py` 的修改失效及历史 → `delivery.py` 的有效版本导出。

画出一次请求的输入、数据库记录、步骤状态、返回码和页面变化即可，不需要背完整仓库。SQL 步骤持久化不是 LangGraph 原生 checkpointer。

## 可复现排错

| 现象 | 先找的证据 | 处理与复验 |
| --- | --- | --- |
| 单独运行 pytest 出现 `No module named 'app'` | 当前目录、`PYTHONPATH`、异常堆栈第一处项目导入 | 根目录用 `PYTHONPATH=backend .venv/bin/python -m pytest ...`；完整检查优先用 `./scripts/check.sh` |
| 409 冲突 | HTTP 响应 `detail`、当前资料/内容/记录版本 | 分清资料冲突、旧批准失效和并发版本冲突；读取当前状态并修正，不自动循环重试 |
| 任务缺证失败 | 任务执行详情、检索范围、引用和材料核验状态 | 核对知识库与品牌，再补授权材料；不要删掉拒答规则强行生成 |
| 页面仍是旧版本 | 前端构建输出、服务地址、浏览器当前 URL | `npm run build` 后刷新；页面并不总是热更新，后端代码变化通常需要重启服务 |
| 在线模型失败（后续真实调用） | 请求 ID、步骤状态、错误类型、供应商账户 | 先检查配置和授权；保留失败和重试成本，不能把未知 token 或账单补成 0 |

## 做一个小修改再验证

复制一条合成参数样本，改成另一个型号；先预测它是否与原型号冲突，再运行固定评测核对。下一次只改一个边界条件，例如缺少版本或定位，解释为什么 API 应拒绝。不要通过删除坏例或把预期改为实际输出来让评测变绿。

```bash
./scripts/check.sh
# 单独验证试点记录（显式隔离配置）
PYTHON_DOTENV_DISABLED=1 SAAS_MODE=false DATABASE_URL=sqlite:///:memory: \
  RAG_RETRIEVAL_MODE=lexical PYTHONPATH=backend \
  .venv/bin/python -m pytest tests/test_pilot.py tests/test_pilot_saas.py -q
```

完成后用自己的话记录：改了哪个文件、原行为、复现输入、为何这样修、测试能证明什么、还不能证明什么。准确表述可以是“我在本机用合成资料复现了修改后重新审核和引用失效阻断”；在线模型、真实平台联调、客户接受、付款与复购必须另有证据。
