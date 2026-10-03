"""Project-specific curriculum. All code references are an explicit allowlist.

This module contains no user data and never calls a model or external service.
Status describes implementation, not a promise that an operator has deployed it.
"""
from copy import deepcopy


SOURCES = {
    "frontend-requests": ("frontend/src/api/client.ts", "export function configureRequestContext", 65),
    "frontend-navigation": ("frontend/src/utils/draftNavigation.ts", "", 85),
    "request-schema": ("backend/app/schemas/agent_run.py", "class AgentRunCreate", 42),
    "database-session": ("backend/app/db/session.py", "def tenant_session", 68),
    "splitter": ("backend/app/agent_core/langchain_adapter.py", "def split_document", 56),
    "rag-index": ("backend/app/agent_core/rag_service.py", "def index_source", 90),
    "rag-answer": ("backend/app/agent_core/rag_service.py", "def answer_question", 75),
    "vector-filter": ("backend/app/agent_core/vector_store.py", "def search_vectors", 35),
    "workflow-graph": ("backend/app/services/content_growth_agent.py", "def build_content_graph", 125),
    "workflow-retry": ("backend/app/services/content_growth_agent.py", "def prepare_retry_agent_run", 59),
    "workflow-recovery": ("backend/app/services/content_growth_agent.py", "def recover_interrupted_agent_runs", 27),
    "review-lifecycle": ("backend/app/services/review_lifecycle.py", "", 110),
    "model-router": ("backend/app/llm/router.py", "    def get_task_client", 34),
    "model-json": ("backend/app/llm/openai_compatible.py", "    def chat_json", 64),
    "output-schema": ("backend/app/services/generation_schema.py", "def normalize_draft_result", 40),
    "saas-permissions": ("backend/app/saas/middleware.py", "def authorize", 25),
    "saas-csrf": ("backend/app/saas/auth.py", "def validate_csrf", 12),
    "saas-context": ("backend/app/saas/context.py", "", 48),
    "rag-evaluation": ("scripts/evaluate_rag.py", "", 95),
    "prediction-rules": ("backend/app/api/routes/predictions.py", "", 110),
    "report-metrics": ("backend/app/api/routes/reports.py", "", 100),
    "safe-fetch": ("backend/app/services/safe_fetch.py", "def normalize_public_url", 53),
    "aihot-adapter": ("backend/app/services/aihot_source.py", "", 80),
    "evidence-policy": ("backend/app/agent_core/evidence_policy.py", "", 95),
    "hybrid-retrieval": ("backend/app/agent_core/hybrid_retrieval.py", "", 125),
    "document-parser": ("backend/app/services/document_parser.py", "", 110),
}


def question(key, title, answer, followups, pitfall, sources):
    return {"id": key, "title": title, "answer": answer,
            "followups": followups, "pitfall": pitfall, "source_ids": sources}


def lab(key, title, explain, reproduce, break_it, diagnose, accept, sources, page):
    return {"id": key, "title": title, "minutes": 30,
            "steps": [{"label": label, "text": text} for label, text in [
                ("解释", explain), ("复现", reproduce), ("故意改坏", break_it),
                ("排错", diagnose), ("验收", accept)]],
            "source_ids": sources, "page": page}


MODULES = [
    {
        "id": "frontend", "title": "页面与交互", "subtitle": "一条操作，怎样走到正确的数据", "order": 1,
        "technologies": ["React 18", "TypeScript", "Vite", "AbortController"],
        "summary": "先从你能看见的页面入手：理解状态、请求、错误和版本，避免点击越来越多却不知道正在编辑哪一份稿件。",
        "implemented": "React 状态驱动页面；请求携带组织上下文；切换组织取消旧请求；编辑入口携带精确稿件与任务 ID。",
        "limitations": "仍有较大的页面组件。客户端校验与按钮隐藏不能替代后端授权，浏览器进度也不等于服务器业务记录。",
        "explanations": [
            "把页面看成状态的投影：加载中、成功、空内容、失败应分别显示。草稿正文属于编辑状态，稿件编号属于身份，不能用列表中的最新一条代替指定编号。",
            "多个请求完成的顺序未必与发起顺序一致。本项目同时使用取消请求和请求代次检查；组织变化后，即使旧响应迟到，也不会覆盖新组织的数据。",
            "组件内状态保存当前交互，服务器保存正式业务结果。学习中心的自测记录只存在当前浏览器，按用户和组织区分；它不是考试认证，也不会自动进入简历。",
        ],
        "docs": [{"title": "React：与外部系统同步", "url": "https://react.dev/learn/synchronizing-with-effects"}],
        "questions": [
            question("frontend-state", "为什么不要在每次渲染时直接发请求？", "渲染应根据已有状态计算界面。网络请求是副作用，通常放在事件处理或 Effect 中。Effect 必须说明依赖并清理旧请求，否则重复执行和迟到响应会造成重复调用、闪回旧数据。开发模式下的额外执行是检查清理逻辑的信号。", ["依赖数组漏掉组织 ID 会发生什么？", "一次点击触发的保存应放在 Effect 还是事件处理函数？"], "把空依赖数组理解成任何场景都只执行一次。", ["frontend-requests"]),
            question("frontend-race", "切换组织后，旧请求突然返回怎么办？", "请求层维护上下文代次。组织或 CSRF 上下文变化时取消在途请求；响应回来后再次比较代次，不一致就抛弃。取消用于减少浪费，代次检查用于防止旧结果写回。后端仍必须按身份隔离数据。", ["AbortController 能撤回已提交的数据库写入吗？", "为什么组织切换要清空页面状态？"], "认为取消前端请求等同于取消服务器任务。", ["frontend-requests"]),
            question("frontend-identity", "任务跳到编辑页，为什么要传 draftId 而不是只传 topicId？", "一个选题可能有多个版本。只传选题再取最新版，可能编辑到与审核任务不同的稿件。本项目按 draftId 精确读取，再核对选题和卡片归属；指定稿不存在时显示错误，而不是悄悄换成另一稿。", ["重新生成一个变体后，原审核是否仍然有效？", "列表缓存应使用什么键？"], "把最新版本当成用户指定版本。", ["frontend-navigation", "review-lifecycle"]),
            question("frontend-types", "有 TypeScript，为什么还要后端校验？", "TypeScript 检查开发时的类型，运行时网络输入仍可能缺字段、带错误范围或被绕过。前端校验改善反馈，后端 Schema 校验保护业务入口，权限校验再决定这个用户能否操作。三者解决的问题不同。", ["类型断言会自动验证接口返回吗？", "422 和 403 分别说明哪一层拒绝？"], "把编译成功当成接口数据一定合法。", ["request-schema", "frontend-requests"]),
        ],
        "labs": [lab("lab-frontend-race", "复现一次迟到响应", "用自己的话画出 A 请求、组织切换、B 请求、A 响应的时间线。", "在隔离测试中运行 frontend/tests/request-context.test.mjs，阅读旧响应不能覆盖新上下文的断言。", "仅在练习分支删除响应后的 generation 检查，并让测试模拟无法及时取消的 A 响应；不要改真实组织数据。", "检查失败断言；区分网络取消与状态写回检查各自保护什么。", "恢复检查，测试通过；能够解释为什么两层保护都要保留。", ["frontend-requests"], "overview")],
    },
    {
        "id": "backend", "title": "接口与数据库", "subtitle": "从一次请求到可信的业务记录", "order": 2,
        "technologies": ["Python", "FastAPI", "Pydantic", "SQLAlchemy 2", "SQLite WAL"],
        "summary": "学习接口输入、数据库事务、错误处理与并发边界，理解页面提示成功的背后必须发生什么。",
        "implemented": "FastAPI 路由与 Pydantic 请求模型；SQLAlchemy 会话；SQLite 外键与 WAL；SaaS 组织独立业务数据库。",
        "limitations": "当前使用单机 SQLite 和应用层恢复；没有完成 PostgreSQL 迁移、分布式事务或多实例并发验收。",
        "explanations": [
            "请求先通过字段类型、长度和范围校验，再做权限及业务状态检查。比如非空字符串仍可能全是空格，因此任务目标还需要业务验证器。",
            "Session 管理一组数据库操作。flush 把变更送入当前事务，commit 才提交；失败后要 rollback，再决定是否重试。Session 不能随意在并发任务间共享。",
            "本项目组织数据库分开保存，减少旧业务表遗漏组织过滤的风险。数据库位置只能来自服务器验证的成员身份，不能直接用浏览器给的组织编号拼接路径。",
        ],
        "docs": [{"title": "FastAPI：请求体", "url": "https://fastapi.tiangolo.com/tutorial/body/"}, {"title": "SQLAlchemy：Session 基础", "url": "https://docs.sqlalchemy.org/en/20/orm/session_basics.html"}, {"title": "Pydantic：模型校验", "url": "https://docs.pydantic.dev/latest/concepts/models/"}],
        "questions": [
            question("backend-schema", "Pydantic 校验和业务校验怎样分工？", "字段类型、长度和数值范围适合放进 Schema。对象是否属于当前组织、审批状态能否修改、额度是否足够，依赖数据库和身份，应在业务边界检查。本项目的任务目标会额外去除空格，避免空白任务通过长度检查。", ["为什么状态转换不能只靠 Literal 类型？", "错误应返回什么信息才便于用户修正？"], "把 Schema 合法等同于业务允许。", ["request-schema", "saas-permissions"]),
            question("backend-transaction", "commit、flush 和 rollback 有什么不同？", "flush 将会话变更写入当前数据库事务，便于取得生成的 ID；commit 提交事务；rollback 撤销尚未提交的变更并恢复会话可用状态。一次业务操作要明确哪些步骤必须一起成功，不能在每一行后随意提交。", ["数据库提交成功但外部调用失败怎么办？", "为什么失败后直接复用同一个 Session 可能继续报错？"], "认为 flush 后的数据已经最终提交。", ["database-session"]),
            question("backend-sqlite", "用了 WAL，SQLite 就能无限并发了吗？", "WAL 能改善读写并行，但 SQLite 的写入仍有约束。本项目面向单机小团队，以短事务、超时和明确失败处理控制复杂度。扩展到多实例时需要重新设计数据库、任务队列和幂等机制，不能只提高 worker 数量。", ["什么操作容易持有事务过久？", "为什么外部模型调用不应占着长数据库写事务？"], "把 WAL 当成分布式数据库能力。", ["database-session", "workflow-retry"]),
            question("backend-idempotent", "用户连续点击两次，如何避免重复导入？", "要区分请求重复与内容重复。内容索引会使用内容哈希和索引配置识别相同内容；业务动作还需考虑唯一约束、请求标识和重试副作用。本项目不能据此宣称所有接口都具备全局幂等，付款等场景尚未实现。", ["相同内容采用不同切块配置，应否重建索引？", "外部操作成功但响应丢失如何重试？"], "只禁用按钮，就声称解决了并发重复请求。", ["rag-index"]),
        ],
        "labs": [
            lab("lab-backend-schema", "让非法任务止步于接口", "解释类型校验、非空校验和权限校验三个边界。", "用测试客户端向任务 Schema 输入正常目标，再输入全空格目标、rag_top_k=0；只用临时数据库或 Schema 实例。", "在练习分支移除 non_blank_goal 验证器，保留 min_length=1，观察空格为什么可以通过。", "定位 Pydantic 错误字段，并检查业务层是否被错误触发。", "恢复验证器，正常目标通过，空格与非法 top_k 拒绝；不产生任务记录。", ["request-schema"], "agent"),
            lab("lab-backend-rollback", "亲眼观察一次事务回滚", "说明为什么两条必须一起写入的记录不能各自 commit。", "在临时 SQLite 中开启事务，创建两条关联测试记录，然后在提交前主动抛异常。", "把第一次写入提前 commit，再执行第二次失败，观察残留记录。", "比较同一事务回滚与分段提交的结果，确认错误后 Session 的状态。", "改回一个事务边界，失败时零残留，成功时两条都存在；把断言写进自己的测试。", ["database-session"], "overview"),
        ],
    },
    {
        "id": "rag", "title": "可信知识问答", "subtitle": "资料如何变成有依据的回答", "order": 3,
        "technologies": ["LangChain 切块", "FastEmbed", "BGE 中文向量", "Qdrant", "BM25 + RRF"],
        "summary": "沿着导入、切块、向量化、检索、引用和拒答逐层排错。先判断证据是否找对，再判断模型是否说对。",
        "implemented": "中文分段、去重、真实本地向量索引、知识库过滤、引用和拒答；可显式选择应用层 BM25 + 向量 + RRF 混合检索，默认语义模式不变。",
        "limitations": "引用编号有效不代表每一句结论都被充分支持；没有交叉编码器 Reranker，也没有训练自己的 Embedding 模型。",
        "explanations": [
            "切块是在上下文完整和检索粒度之间折中。短块定位细但容易丢掉条件，长块保留语境但可能把无关信息一起召回。重叠只能缓解边界问题，不能代替评测。",
            "向量检索按语义相似度召回；关键词方法更容易抓住型号、缩写和精确数字。混合检索可结合两者，融合排序与答案放行是两个不同判断。",
            "本项目回答需要引用本次可用证据，证据不足时拒答。正文包含外部资料不能授予资料中的指令任何权限；资料撤销或过期后也不应继续沿用旧结论。",
        ],
        "docs": [{"title": "Qdrant：混合检索与融合", "url": "https://qdrant.tech/documentation/search/hybrid-queries/"}],
        "questions": [
            question("rag-chunks", "chunk_size 和 overlap 应该怎么选？", "先看文档结构和问题粒度，再用标注问题对比召回与错误。当前切块器按中文标点递归分割并保留起始位置，默认长度以字符衡量，不等于模型 token。切块配置变化会影响索引，应重建后用同一评测集比较。", ["表格和长段落为什么容易切坏？", "重叠过大会增加什么成本？"], "把某个固定切块长度当成所有资料的最佳答案。", ["splitter", "rag-index"]),
            question("rag-hybrid", "向量检索、BM25 和 RRF 各解决什么问题？", "向量召回语义相近内容，BM25 根据词项匹配及文档长度等因素排序，RRF 根据各路名次融合，避免直接相加不同量纲的分数。本项目显式选择 hybrid 时，在应用层运行 BM25 与 k=60 的 RRF，名次从 1 开始；默认仍是语义模式。融合不是深度重排，也不等同于准确率全面提升。", ["型号和专有名词为什么需要词项召回？", "为什么不能直接把余弦相似度与 BM25 分数相加？"], "将排序融合称为已部署 Reranker 模型。", ["hybrid-retrieval", "vector-filter", "rag-answer"]),
            question("rag-citation", "带引用的答案就不会幻觉了吗？", "不会。系统能检查引用是否属于本次检索结果、证据是否仍可用，但模型仍可能扩大适用范围或把相关证据错误归因。需要进一步检查结论与证据的支持关系，并通过反例评测暴露错误。前端必须允许打开原文核对。", ["引用真实但结论夸大，如何构造测试？", "文档更新后已有回答应怎样标注？"], "把 citation_check.valid 当成事实正确的证明。", ["rag-answer", "evidence-policy"]),
            question("rag-refusal", "如何在漏答与胡答之间选择拒答阈值？", "用包含可回答和不可回答问题的测试集共同观察。门槛过高会漏掉可用证据，过低会让无关片段进入生成。本项目保留拒答结果，不应该靠偷偷降低阈值让演示全通过；还要区分检索失败、证据不足和模型失败。", ["无答案问题在数据集中应占多少？", "检索找到资料但生成错误，应改检索还是提示词？"], "只测命中题，忽略误拒与错误放行。", ["rag-answer", "rag-evaluation"]),
        ],
        "labs": [
            lab("lab-rag-citation", "做一次能回答与应拒答的对照", "解释检索命中、引用有效和结论正确三件事的区别。", "在独立练习知识库导入合成资料：试点只提供 Markdown 导出、不提供自动发布；分别询问导出方式和自动发布渠道。", "仅修改提问，诱导系统把其他平台能力归给试点；不要关闭真实系统的证据校验。", "查看命中片段与答案引用，区分误拒、越界结论、引用不支持。", "可回答问题有对应引用，无依据能力不被承诺；记录失败案例而不是删除它。", ["rag-answer", "evidence-policy"], "rag"),
            lab("lab-rag-chunks", "对比两种切块设置", "说明改变切块会同时改变召回粒度、重复片段与索引数量。", "在临时测试目录用同一份合成文档分别按 300/50 和 900/140 字符切块，记录同一问题的命中内容。", "把关键条件故意放在段落边界，使用零重叠小块观察条件丢失。", "比对 chunk 起始位置、条件所在块和答案是否遗漏限制。", "保存两组结果，说明哪组适合这份资料及其反例；不能用单题结果宣称整体提升。", ["splitter", "rag-index"], "rag"),
        ],
    },
    {
        "id": "agent", "title": "AI 内容工作流", "subtitle": "从模型调用到可恢复的任务", "order": 4,
        "technologies": ["LangGraph StateGraph", "条件路由", "模型路由", "JSON 校验", "人工审核"],
        "summary": "理解为什么生产流程需要状态、失败出口和人工判断，以及为什么调用模型成功还不等于任务完成。",
        "implemented": "内容任务使用 LangGraph 条件工作流；步骤结果存 SQL；支持失败重试、人工审核、模型 JSON 解析与有限修复。",
        "limitations": "没有使用 LangGraph 原生 checkpointer；启动恢复把中断任务标为可处理状态，不能宣称断电后自动恰好一次续跑。没有自主全网研究 Agent。",
        "explanations": [
            "工作流把任务拆成检索、选题、正文、卡片、检查、评估等节点。条件分支决定质量不足时是否修订，用户仍需要审核成品。图有分支不等于模型可以自行做所有决策。",
            "步骤写入数据库可帮助定位失败和显式重试，但数据库状态不自动变成消息队列。服务中断、任务重放和外部副作用都需要明确处理。",
            "模型路由统一供应商选择；JSON 能解析只是第一关，字段范围和业务含义还要验证。自动修复应有限次，并保留失败可见性，不能无止境重复收费。",
        ],
        "docs": [{"title": "LangGraph：持久化与 checkpointer", "url": "https://docs.langchain.com/oss/python/langgraph/persistence"}],
        "questions": [
            question("agent-graph", "你的项目为什么使用 LangGraph？", "需要把多个生成和验证阶段组织成可观察的条件流程：节点负责一项工作，边控制先后，质量结果驱动分支。项目确实构建并执行 StateGraph，但节点大部分是预先设计的业务逻辑，因此应称为受控工作流，而不是无限自主 Agent。", ["用普通函数能否实现？你为什么仍选择图？", "哪个条件会进入修订分支？"], "把引入框架当成已经实现自主规划。", ["workflow-graph"]),
            question("agent-recovery", "步骤存数据库，和 LangGraph checkpointer 有什么区别？", "本项目保存业务步骤结果并在重试时决定复用或重跑，启动时处理遗留运行状态。LangGraph checkpointer 是框架级状态持久化机制，项目目前没接入。不能把两者混称，也不能保证进程中断后的外部操作恰好执行一次。", ["失败节点前的模型调用是否需要重跑？", "未来引入队列后如何避免两个 worker 执行同一任务？"], "认为有日志就拥有可靠断点续跑。", ["workflow-retry", "workflow-recovery"]),
            question("agent-json", "模型输出合法 JSON，为什么仍然可能不能用？", "JSON 只说明语法可解析，不能保证字段齐全、分数在范围内、文本有证据或业务状态允许写入。本项目做对象检查和生成结果规范化，并限制修复次数；规范化带有默认值，所以还应检查是否掩盖质量问题。", ["把缺失正文补成默认文案有什么风险？", "供应商不支持严格结构化输出时怎么办？"], "把 JSON 解析成功当作内容质量验收。", ["model-json", "output-schema"]),
            question("agent-human", "为什么人工审核后改稿要让审批失效？", "审批针对具体内容与证据版本。正文、卡片或派生稿改变后，旧审批不能自动覆盖新内容。本项目维护任务、稿件及审核状态之间的关系；清单勾完和正式批准也是不同状态。导出文件本身不等于对外发布。", ["稿件变体应继承哪些元数据，不能继承什么？", "同一人既编辑又审批是否足够满足所有客户？"], "把审核结果绑定选题名称，而不是内容版本。", ["review-lifecycle", "workflow-graph"]),
        ],
        "labs": [lab("lab-agent-review", "走一遍退回、修改与重审", "先列出任务状态、稿件状态和检查清单，说明三者的差别。", "用合成素材和本地规则创建任务，进入待审核，退回后从该任务打开指定稿件。", "在练习稿中改动正文或生成变体，观察原批准不能继续沿用；不对真实客户内容做实验。", "核对 runId、draftId、卡片归属与重新提交按钮，查阅 review_lifecycle。", "回到原任务重新提交并审批，导出对应版本；旧审批不能错误覆盖改稿。", ["review-lifecycle", "frontend-navigation"], "agent")],
    },
    {
        "id": "saas", "title": "团队与安全边界", "subtitle": "为什么别人的资料不能出现在我的回答里", "order": 5,
        "technologies": ["RBAC", "ContextVar", "HttpOnly Cookie", "CSRF", "组织隔离"],
        "summary": "理解身份认证、权限授权、数据隔离与审核的差异，知道本地免登录与团队模式的适用边界。",
        "implemented": "团队模式具有会话、成员角色、组织数据库与向量目录隔离、CSRF 和来源校验。本地入口免登录且绑定本机。",
        "limitations": "尚未完成公网生产验收；本地免登录不是共享访问方式。未提供企业 SSO、MFA、邮件找回或完整支付系统。",
        "explanations": [
            "认证回答你是谁，授权回答你能做什么，租户隔离回答你的数据在哪里。页面隐藏按钮只能改善交互，服务器才是权限决策点。",
            "组织编号必须经过当前用户成员关系校验，再写入请求上下文。数据库和向量检索都需要同一可信边界，否则主表隔离了也可能从向量库泄漏片段。",
            "浏览器会自动携带 Cookie，所以修改操作需要额外确认请求来源。HttpOnly 减少脚本读取 Cookie 的机会，CSRF 防止冒用浏览器会话，两者并不互相替代。",
        ],
        "docs": [{"title": "OWASP：CSRF 防护", "url": "https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html"}],
        "questions": [
            question("saas-rbac", "RBAC 应放在前端还是后端？", "后端必须按角色拒绝越权请求，前端再隐藏或禁用无权限操作。当前 owner、editor、reviewer、viewer 对应不同写入与审核能力。只读角色也可能允许使用查询接口，因为查询可能使用 POST；不能只用 HTTP 方法判断全部权限。", ["为什么问答 POST 不等于普通数据修改？", "审核人员与内容编辑能否互相替代？"], "按钮看不到就认为接口不会被调用。", ["saas-permissions"]),
            question("saas-tenant", "怎样防止修改 organization_id 越权？", "浏览器可发送组织选择，但服务器必须确认用户属于该组织，之后才建立 TenantContext。本项目使用该上下文选择独立业务库和向量目录。workspace_id 与 knowledge_base_id 还需在组织内检查，不能让客户端值直接决定文件路径。", ["后台任务怎样继承组织边界？", "切换组织后模型客户端缓存是否含用户资料？"], "仅在 SQL 中多加一个来自客户端的过滤条件就宣称隔离完成。", ["saas-context", "database-session", "vector-filter"]),
            question("saas-csrf", "已经有 HttpOnly Cookie，为什么还要 CSRF？", "HttpOnly 限制脚本读取 Cookie，不阻止浏览器在请求中自动携带它。项目团队模式的变更接口还验证 CSRF 令牌及 Origin；Host 校验确认服务地址。令牌、来源和会话需一起检查，不能靠前端约定。", ["为什么 GET 不应承担删除等副作用？", "XSS 与 CSRF 的防护是否相同？"], "认为 Cookie 看不见就不会被跨站请求利用。", ["saas-csrf", "saas-permissions"]),
            question("saas-local", "为什么开发时免登录，团队版却不能直接关闭鉴权？", "本地模式便于单人使用，服务绑定本机；团队模式则依赖认证来确定数据库与权限。关闭团队鉴权会破坏隔离的根基。本项目保留两种明确启动方式和独立数据位置，而不是给团队接口增加万能绕过。", ["本地服务监听 0.0.0.0 会改变什么风险？", "怎么向面试官证明两个组织互相看不到数据？"], "把本地免登录部署到公网当成正式 SaaS。", ["saas-context", "database-session"]),
        ],
        "labs": [lab("lab-saas-isolation", "证明租户隔离，而不只看登录页", "说明两个组织分别拥有什么数据库、向量目录和权限。", "运行 tests/test_saas_isolation.py，在临时目录为两个合成组织建立同名但不同内容的资料。", "只在测试请求中把组织或知识库 ID 改成另一个组织；不使用真实用户凭证。", "检查请求是否在读取数据前被拒绝，查询结果是否包含其他组织片段。", "读取、检索与编辑均不能跨组织；关闭前端按钮也不能影响服务器断言。", ["database-session", "saas-context", "vector-filter"], "overview")],
    },
    {
        "id": "evaluation", "title": "评测与业务复盘", "subtitle": "用失败案例说明系统能做到什么", "order": 6,
        "technologies": ["pytest", "契约测试", "检索评测", "冻结预测", "累计快照"],
        "summary": "学习测试、AI 评测和业务指标的分工，避免漂亮分数掩盖错误答案或不真实的效果。",
        "implemented": "接口与安全回归测试；人工合成检索评测集；发布前预测锁定；人工录入指标并复盘，缺失与真实零值分开处理。",
        "limitations": "没有真实客户业务效果基准。合成小样本不能代表生产准确率；模型自评分不等于独立验收分数。",
        "explanations": [
            "单元测试检查规则是否执行，检索评测检查证据能否找到，生成评测检查结论与证据的关系，真实试点再观察用户是否省时。四种证据不能互相替代。",
            "评测集应包含同义问法、精确型号、无答案、过期证据和权限负例。调参使用的题和最终比较的题应分开，并保留失败样本。",
            "效果数据为空表示不知道，为零表示观察到零。平台累计数据是一张快照，多次录入不能直接相加。预测必须在结果出现前固定，否则复盘会受到事后信息污染。",
        ],
        "docs": [{"title": "pytest：测试实践", "url": "https://docs.pytest.org/en/stable/how-to/assert.html"}],
        "questions": [
            question("evaluation-layers", "几百项测试通过，能说明 AI 回答准确吗？", "不能。测试通常证明代码契约、安全边界与已知回归成立；回答准确度需要带预期证据和无答案标签的独立问题集。还应单独测检索与生成，以免模型流畅表达掩盖证据没找对。", ["合成测试资料与真实用户资料有何差距？", "如何记录一个引用正确但结论错误的案例？"], "把测试通过率当成模型准确率。", ["rag-evaluation", "rag-answer"]),
            question("evaluation-metrics", "Recall@k 与答案正确率有什么区别？", "Recall@k 衡量预期相关资料是否进入前 k 个结果；答案正确率衡量最终结论。召回正确但生成仍可能误读，召回错误也可能被模型常识掩盖。使用指标时先声明按文档还是按片段统计，以及无答案题怎样计分。", ["多个正确证据时分母怎么定义？", "检索更快但误拒更多，是否值得上线？"], "只给一个综合分，不解释定义与失败类型。", ["rag-evaluation"]),
            question("evaluation-missing", "没有录入阅读量，为什么不能当成零？", "缺失表示尚未观察到，真实零表示已经观察且结果为零。把两者混合会扭曲排名、均值和预测误差。项目报告分别统计缺失与已录入数量；只有已记录指标才进入相应分母。", ["阅读量为零时互动率应该怎么显示？", "重复提交累计快照如何避免翻倍？"], "用 0 填补空缺后直接得出业务好坏结论。", ["report-metrics"]),
            question("evaluation-leakage", "为什么发布后的预测不能作为事前预测参与校准？", "事后已经知道一部分结果，再填预测会泄漏未来信息，让误差人为变小。项目锁定已确定基准并限制重关联；真实校准应固定预测时点、发布时点和观察窗口。更低误差只有在同样规则下才有意义。", ["预测 1000、实际 100，改成 100 会破坏什么？", "平台数据持续增长，哪一个时点用于比较？"], "认为能修改预测更方便，因此不需要冻结历史。", ["prediction-rules", "report-metrics"]),
        ],
        "labs": [lab("lab-evaluation-baseline", "发现一次看起来完美的错误报告", "解释事前基准、观察窗口、缺失值与累计快照。", "阅读并运行 tests/test_prediction_reporting.py，使用合成预测 1000、实际 100 和一条没有指标的发布记录。", "在测试副本里尝试事后把预测改成 100，或把缺失指标当零并进入均值。", "检查接口拒绝与报表分母；累计快照应采用当前观察值，而不是把历史快照相加。", "原预测不能被改写，缺失不参与对应统计，真实零可参与；说明误差仍为原来的差距。", ["prediction-rules", "report-metrics"], "reports")],
    },
    {
        "id": "connectors", "title": "信源与扩展", "subtitle": "把外部信息安全地接入业务", "order": 7,
        "technologies": ["HTTPX", "REST API", "pypdf", "DOCX XML", "SSRF 防护", "MCP 概念"],
        "summary": "理解网页嵌入、API、MCP 和资料导入的区别，避免把能看到资讯误认为有权商业分发或自动采取行动。",
        "implemented": "AIHOT 与 GitHub 信源适配；安全 URL 抓取；可审核证据；文本 PDF、DOCX、MD/TXT 提取预览，用户确认后入库。Web 资讯使用 REST，不是自建 MCP Server。",
        "limitations": "AIHOT 官方 MCP 验证与 Web 工作流是两条路径；内容 Agent 尚未自主调用新闻 MCP。Obsidian 双向同步、OCR、自动发布和平台指标自动采集仍需后续实现。",
        "explanations": [
            "iframe 只把另一个网页显示进来，还受对方嵌入策略限制。API 返回可处理的数据；MCP 规定客户端与服务器如何发现和调用工具、资源等能力，但不会自动解决授权、商业使用和数据质量。",
            "外部网页与文档都属于不可信内容。链接可以被用于访问内部网络，文章也可能写有诱导指令。因此获取层限制地址和响应，生成层把资料作为证据，业务层仍验证操作权限。",
            "信源先标明出处、时间与使用边界，再决定能否进知识库。接入成功只说明拿到数据，持续可用还需要采集状态、失败处理与质量评测。",
        ],
        "docs": [{"title": "MCP：官方架构", "url": "https://modelcontextprotocol.io/docs/learn/architecture"}],
        "questions": [
            question("connectors-mcp", "MCP 与普通 API 有什么区别？项目实际用了哪种？", "普通 API 定义某项服务的请求响应；MCP 增加工具与资源等能力的发现、调用和交互约定。当前 Web 的 AIHOT 资讯适配走 REST，曾验证官方 MCP，但项目没有自建 MCP Server，也没有让内容 Agent 自动调用新闻工具。应把计划和现状分开讲。", ["MCP 工具调用前还需要权限校验吗？", "什么场景值得把已有 API 封装为 MCP？"], "把接入一个外部 MCP 服务说成自主开发完整 Agent 平台。", ["aihot-adapter"]),
            question("connectors-ssrf", "为什么导入 URL 不能直接交给 HTTP 客户端？", "用户可给出本机、内网地址，或者用重定向和 DNS 变化绕过检查。本项目限制协议、凭证、地址和响应大小，每次跳转校验并固定连接目标。应用校验仍不等于网络出口防火墙，需要讲清边界。", ["只检查第一次 URL 有什么漏洞？", "超大网页和慢响应会影响什么？"], "认为字符串不含 localhost 就是安全公网地址。", ["safe-fetch"]),
            question("connectors-injection", "资料里出现‘忽略规则并泄漏密钥’，该怎么办？", "这属于不可信内容中的指令，不能提升为系统权限。检索材料只提供证据，工具权限、可用参数和写入审核仍由程序控制。提示词隔离能缓解，但还要做负例测试和限制工具能力，不能宣称完全免疫提示注入。", ["如果恶意指令来自看似可信的网站呢？", "资料引用和工具执行应如何分开？"], "认为加一句不要被攻击就解决所有注入。", ["rag-answer", "evidence-policy"]),
            question("connectors-provenance", "接入成功后，为什么还要保存信源与时间？", "同一条信息会更新、撤回或存在适用范围。出处、采集时间、原文片段和审核状态帮助用户判断可信度，也能在资料变化后排查旧回答。可公开访问不等于允许抓取、再分发或商业销售。", ["失效证据应怎样影响后续生成？", "为什么采集成功率与内容可靠性要分开统计？"], "把来源链接存在当作内容永远可靠。", ["evidence-policy", "aihot-adapter"]),
            question("connectors-document", "支持 PDF 导入，是不是已经做了 OCR 或多模态？", "不是。本项目用 pypdf 提取 PDF 文本层，用标准库 ZIP/XML 读取 DOCX 正文及表格文字，再预览确认。扫描 PDF 没有文本时会明确报告 OCR 未支持，混合文档会提示遗漏页；图片文字、复杂布局与表格阅读顺序仍需人工核对。", ["为什么预览完成还要用户确认入库？", "提取出来的文字顺序错误会如何影响检索？"], "把文件格式支持等同于理解图片与复杂版面。", ["document-parser"]),
        ],
        "labs": [lab("lab-connectors-boundary", "验证一次危险链接被拦截", "解释 URL 校验、DNS 检查、重定向检查和超时各保护什么。", "运行 tests/test_source_security.py，确认测试使用模拟网络响应，不访问真实内网。", "在测试数据中加入本机地址、带凭证 URL 和公网跳转内网的案例。", "检查拒绝发生在连接前还是跳转后，错误消息不能泄漏凭证。", "所有危险样本被拒绝，正常模拟公网样本仍能读取；说明为什么还需要部署层出口控制。", ["safe-fetch"], "source-hub"),
                 lab("lab-document-preview", "导入成功前，先检查有没有丢文字", "解释文本 PDF 与扫描 PDF、DOCX 正文与图片之间的区别。", "使用不含个人信息的合成文本 PDF 或 DOCX，导入预览并核对段落，确认后才保存到练习知识库。", "换成没有文本层的扫描 PDF，或在 DOCX 放一张只有图片文字的截图。", "查看 OCR 未支持或图片遗漏提示，确认失败与预览阶段不会创建已索引文档。", "正常资料预览确认后可检索；无文本文件明确失败；遗漏内容有提示，没有把空内容当完整资料入库。", ["document-parser"], "knowledge")],
    },
]


def get_catalog() -> dict:
    """Return independent data so callers cannot mutate the shared curriculum."""
    return deepcopy({
        "version": "2026-09-19.1", "title": "读懂你自己的系统",
        "notice": "课程以当前项目代码为依据。已实现不等于生产验收；自测是你的学习记录，不是能力认证。",
        "progress_notice": "学习记录只保存在本浏览器，按当前模式、用户和组织隔离；清理浏览器数据会删除记录，不会上传学习答案。",
        "modules": MODULES,
        "sources": [{"id": key, "path": spec[0]} for key, spec in SOURCES.items()],
        "next_technologies": [
            {"name": "交叉编码器 Reranker", "status": "学习方向", "reason": "先测混合召回的失败样本，再评估精排收益与推理成本。当前未部署。", "module": "rag"},
            {"name": "LangGraph checkpointer 与持久化任务队列", "status": "学习方向", "reason": "解决多进程恢复和任务调度前，要先明确幂等、重放与外部副作用。当前是 SQL 步骤记录。", "module": "agent"},
            {"name": "MCP 工具服务", "status": "学习方向", "reason": "需要模型外部工具调用场景时，再封装受控、可审计的工具；当前没有自建 MCP Server。", "module": "connectors"},
            {"name": "结论与证据一致性评测", "status": "待验证", "reason": "补足引用存在性检查的边界，先建立人工标签，再比较自动评审与人工结果。", "module": "evaluation"},
        ],
    })
