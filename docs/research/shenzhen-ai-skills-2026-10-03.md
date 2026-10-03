# 深圳 AI 应用求职能力与项目改进依据

核查日期：2026-10-03。适用读者：准备深圳 AI 应用、企业提效或实施交付岗位的初级开发者。招聘样本按毕业届别和经验要求分别判断，不代表维护者的个人求职状态。

本报告连接招聘证据、现有代码和可演示动作。项目功能存在，不代表作者已能独立实现、解释或交付；源码阅读、自动测试、本机演示、线上运行和客户验收是不同层级的证据。

## 结论与技术取舍

现有 Python/FastAPI、SQLAlchemy/SQLite、LangChain 文档处理、Qdrant/FastEmbed、LangGraph、React/TypeScript 已覆盖所核查岗位的主要应用工程方向。当前更值得补齐的是可验证的业务流程、效果评估、故障定位和系统接入能力，而不是继续增加技术名称。

优先顺序：

1. 能解释并修改 Python/API 和 SQL 数据流。
2. 能验证 RAG 资料范围、引用、拒答和失效处理。
3. 能解释 LangGraph 条件流程、人工审核、失败重试与副作用边界。
4. 能通过固定测试集和真实试点记录，区分技术质量与业务结果。
5. 能完成轻量部署、日志定位、备份恢复与交接。
6. 有真实业务需求后，再选择 Dify、n8n 或企业办公系统中的一个完成接入。

保留适合内存有限设备的单进程、SQLite WAL、Qdrant Local 和按需模型 API 路线。当前不因社招 JD 出现某个名词，就加入 K8s、分布式队列、微调训练、多套编排平台或本地大模型。框架选择服务于问题，后续扩容应由实际并发、隔离和运维需求触发。

## 招聘证据与资格分层

这是定向寻找的便利样本，不是深圳岗位普查。不计算市场占比，也不据此预测薪资或录用概率。页面存在、搜索索引可读或有“立即申请”，均不能证明招聘方仍有名额。

### 上一轮已在登录 BOSS 中读取的岗位

以下四条来自本会话上一轮 2026-10-03 的职位正文核查；本轮未重新进入用户 Chrome。保留来源以便复查，不将旧页面核查写成此次重新确认的招聘状态。

| 公司与来源 | 资格与实际工作 | 对能力路线的启示 |
| --- | --- | --- |
| [半岛医疗：内部 AI 提效 FDE](https://www.zhipin.com/job_detail/a71a23ab4445e2790nJ92Ny0ElZS.html) | 接受 2025–2027 届；业务调研、脚本/API、数据处理、企业系统对接；“人才孵化营”的合同和培养条件需另行确认 | 最贴近业务理解、原型、集成、反馈、复用案例的完整过程 |
| [思倍云：初级 FDE](https://www.zhipin.com/job_detail/a61a9589393f9ddd0nN52tq8GFdR.html) | 接受 2025/2026 届；资深人员带教；园区数字化、物联网实施，涉及现场调试、培训、验收及短期驻场 | 可支撑实施交付方向；AI 开发占比未证实，不能当作纯大模型开发岗位 |
| [乐能创新：AI 应用运营专员（跨境）](https://www.zhipin.com/job_detail/94e19cf2489e661a0nN829W9E1RR.html) | 经验不限，跨境经验优先；售后 AI 工具、知识库、工单分类、数据分析、培训和效果复盘 | 业务数据、知识库质量、采用效果及沟通能力有价值；是否接受已毕业应届生、是否有工程协作仍需核实 |
| [广联智通：AI 应用开发工程师](https://www.zhipin.com/job_detail/5e33fdfbe83e89030nN40t61GFZU.html) | 石岩；流程梳理、LLM/RAG/Agent 应用和复用组件；列表“1 年以内”与正文“1 年以上”不一致 | 与项目技术相关，但不能列为已确认无经验可投岗位 |

### 本轮补充的一手或企业直接发布材料

| 编号与来源 | 日期、资格和证据强度 | 明确出现的能力及使用方式 |
| --- | --- | --- |
| S1 [半岛医疗 FDE：天津工业大学承载的企业招聘正文](https://jobs.tiangong.edu.cn/correcruit/content/id/56062.html) | 发布 2026-09-11；本轮完整打开；明确 25–27 届本科，和上一轮 BOSS 内容交叉印证 | Python 优先，脚本、API、数据处理、Prompt、工作流实践、业务沟通、企业系统对接和可复用案例。Coze/Dify/n8n/LangChain 是示例选项，不要求同时使用全部平台 |
| S2 [OPPO：2026 届算法测试开发工程师](https://careers.oppo.com/university/oppo/campus/post/1559?recruitType=Graduate) | 发布 2026-01-27；官网搜索索引可读正文，直接打开没有正文；深圳/北京/成都可选，当前是否仍开放未确认 | 至少一种编程语言、Linux 和数据库操作；AI 验收、Benchmark、测试工具、缺陷分析、质量监控。Python 是可选语言之一；复杂系统和评测经历是优先项。用于支持评测能力方向 |
| S3 [招银金融信息服务：AI 应用与数据工程师](https://yjs.job1001.com/job_detail/53700436.htm?uid=cm1467872752476) | 更新 2026-06-10；企业招聘平台页面索引可读，直接打开超时；深圳南山，应届、经验不限；页头本科与正文大专条件不一致 | 数据提取、清洗、结构化，低代码测试验证、数据集与应用效果迭代。该 JD 未明确 Python、FastAPI，不能替招聘方添加要求 |
| S4 [熵基律动：企业 2026 校招 PDF](https://career.cuhk.edu.cn/attachment/careercuhk/ueditor/file/20260515/2071_%E7%86%B5%E5%9F%BA%E5%BE%8B%E5%8A%A8%20-%202026%E6%A0%A1%E5%9B%AD%E6%8B%9B%E8%81%98.pdf) | 高校承载企业材料，2026-05；PDF 搜索索引可读，文件抓取超时，未完整审阅；[招聘会页](https://career.cuhk.edu.cn/job/view/id/468236)显示活动已结束，PDF 索引称投递截至 2026-12-31；部分岗位另有经验年限 | API 工具调用、流式/上下文、RAG、Python 异步、FastAPI/Pydantic、PostgreSQL/Redis、小步交付；同时列应届友好的基础能力。仅作成长参照，不声称所有条目都是初级门槛 |
| S5 [华润：27 届助理研发工程师，智能体开发运营](https://crc.wintalent.cn/wt/CRC/web/templet1000/index/corpwebPosition1000CRC%21getOnePosition?brandCode=1&lanType=1&postIdEnc=ae46045d11d296df&recruitType=1&showComp=true) | [官网列表](https://crc.wintalent.cn/wt/crn/web/index/campus)发布 2026-09-18；本轮完整打开 JD；深圳/西安，本科、硕士优先；明确 27 届，不属于用户当前可投证据 | Python、Prompt、API 调试、业务需求转方案；Dify 等平台的知识库、编排、插件、效果测试、问题定位、上线运营。向量库和原型实践是优先项 |
| S6 [EcoFlow：AI 应用开发工程师](https://www.nowcoder.com/jobs/detail/464289) | 企业校招 HR 直接发布，本轮完整打开；2026-09-01 起；深圳南山，本科，明确 2027 届；平台结束日期异常地到 2029 年，不采用它判断期限 | Python 基础；Agent/RAG、Workflow、Prompt、HTTP 业务集成；工具或课程项目经历加分。只作初级能力参照，不能给 2026 届用户作为明确可投职位 |
| S7 [盈达：大模型应用开发工程师](https://idata.zhiye.com/zpdetail/190806713?p=1%5E4) | 官网发布 2026-03-17，本轮打开；深圳社招；硕士或有 3 年以上经验放宽本科，用户当前门槛不符 | Python/React/Flask/Dify、Agent/RAG/向量库/MCP；Docker/Linux 是加分项。用于中期成长方向，不能直接转化成现在必须补完的列表 |

另有 [中兴新云 2026 届 AI 产品实施顾问](https://career.gdut.edu.cn/campus/view/id/1020229)：高校承载企业校招材料，索引明确本科及以上、工作城市包含深圳，涉及需求、选型、部署、上线和方法论；具体深圳岗位分配及开放状态未确认。

反复出现的是基础编程/API、知识与数据处理、工作流、业务沟通和效果验证。不同岗位的门槛不同：FDE 更关注快速落地，应用开发更看工程实现，运营/实施更看流程、数据、培训和验收。不能把三条路线所有要求并成一个“必须全会”的技术清单。

## 招聘能力到现有代码及面试演示

下列路径在本轮只读核对；表中的“演示”是下一次本人需要完成的动作，不表示本报告已经执行成功。

| 能力 | 当前代码证据 | 可面试演示动作 | 当前边界或缺口 |
| --- | --- | --- | --- |
| Python、API、输入校验 | `backend/app/api/routes/agent_runs.py`、`backend/app/schemas/agent_run.py`、`backend/app/api/routes/pilot.py`、`backend/app/schemas/pilot.py` | 追踪一个请求从页面到 Pydantic、service、SQL；提交错误类型和过期版本，解释 422/409；修改一个业务字段并验证 | 有接口不等于能独立开发。需要本人能解释异常处理、事务和并发覆盖问题 |
| SQL 与数据建模 | `backend/app/db/session.py`、`backend/app/models/agent_run.py`、`backend/app/models/pilot_record.py`、`backend/app/services/pilot.py` | 查看任务与试点记录外连接；解释未登记任务为何保留，空工时与零工时区别；核对日期筛选 | 当前为轻量 SQLite 路线；不能声称已具备 PostgreSQL/Redis 生产运维经验 |
| RAG 资料与证据 | `backend/app/services/document_parser.py`、`backend/app/agent_core/langchain_adapter.py`、`backend/app/agent_core/rag_service.py`、`backend/app/agent_core/evidence_policy.py` | 导入授权测试资料，展示切块、来源和引用；问一个资料内问题及一个无依据问题；使旧资料失效后再次查询 | 引用存在不等于结论被证据支持。OCR、复杂文档完整解析和真实客户效果未由这些文件证明 |
| 向量/混合检索 | `backend/app/agent_core/embeddings.py`、`backend/app/agent_core/vector_store.py`、`backend/app/agent_core/hybrid_retrieval.py` | 对同一固定问题集比较 lexical/semantic/hybrid，解释一个胜例和一个坏例，核对知识库范围 | FastEmbed+BGE、Qdrant Local、BM25+RRF；RRF 不是交叉编码器重排，混合也不保证总是更好 |
| LangGraph 与人工审核 | `backend/app/services/content_growth_agent.py`、`backend/app/services/review_lifecycle.py`、`backend/app/services/delivery.py`、`tests/test_workflow_states.py`、`tests/test_delivery.py` | 展示检索→生成→检查→有限修订→待审核；退回修订；修改已批准正文使旧批准失效；解释失败节点重试 | 实际使用 StateGraph，步骤恢复依靠 SQL 记录；不是 LangGraph 原生 checkpointer，也不是分布式任务系统。取消在节点边界生效，已发出的模型请求不能撤回 |
| 效果评估与测试 | `scripts/evaluate_rag.py`、`scripts/evaluate_evidence.py`、`scripts/evaluate_hybrid_retrieval.py`、`scripts/fixtures/`、`backend/app/services/package_evaluator.py` | 在独立临时数据运行固定检索集，检查错误接纳/误拒和坏例；修改策略后重跑；解释模型评分与人工验收差别 | 混合检索脚本明确使用人工中文小样本，不是独立真实客户测试集，未评测生成质量；模型评分不能代替人工批准 |
| 模型 API 与问题定位 | `backend/app/llm/router.py`、`backend/app/llm/openai_compatible.py`、`backend/app/models/model_run.py`、`tests/test_model_client.py` | 演示配置检查、模型调用失败、错误类型和调用记录；说明 token/耗时字段来自哪里 | 本地模式为规则与摘录，不是本地 LLM；调用额度不是精确人民币账单；未核验的供应商配置不能写成已联调 |
| 外部系统集成 | `backend/app/services/aihot_source.py`、`backend/app/services/github_source.py`、`backend/app/services/safe_fetch.py`、`backend/app/api/routes/source_hub.py` | 对已有授权来源解释 HTTP 请求、解析、错误处理、资料入库和来源权利边界 | 有 REST 适配经验，不等于已集成 ERP/CRM、飞书、企业微信、Dify 或 n8n；外部 MCP 配置也不是自建 MCP 服务 |
| 部署、健康与备份 | `Dockerfile`、`compose.yaml`、`compose.saas.yaml`、`backend/app/api/routes/health.py`、`scripts/start.sh`、`scripts/saas_backup.py`、`docs/saas-operations.md` | 从环境检查启动，访问健康接口；在隔离目录备份恢复；按日志排查一次可控错误 | 本报告只确认配置存在，未执行 Docker 或恢复演练。默认本机监听、Qdrant Local 单进程，不可描述为公网生产高可用 |
| 业务交付与复盘 | `backend/app/services/business_brief.py`、`backend/app/services/delivery.py`、`backend/app/services/pilot.py`、`frontend/src/pages/PilotPage.tsx` | 用一个客户授权的内容任务，解释目标、批准资料、交付版本、客户反馈、返工与人工投入 | 软件支持登记不等于已有客户、付费、复购或节省工时证据。团队模式也没有品牌级成员权限 |

## 本轮切片：试点验收与人工工时

本轮正在增加和验证的范围是：

- `GET /api/pilot/records/{run_id}`：读取任务的人工试点记录。
- `PUT /api/pilot/records/{run_id}`：保存反馈、基线、实际操作与实施支持工时，使用记录版本防止覆盖。
- `GET /api/pilot/report?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`：按任务创建日的 UTC 日期范围汇总，保留未登记、待确认、退回、放弃及旧版本验收。
- `frontend/src/pages/PilotPage.tsx`：人工登记、日期查询、查看当前可比任务工时和导出复盘 JSON。

记录字段包括试点批次、反馈结果、原流程基线、实际操作工时、实施支持工时、基线来源、客户确认依据和备注。填写基线时需要来源；记录验收通过时需要确认依据，并通过现有正式交付检查绑定内容版本。修改内容或引用失效后，旧验收不能继续当作当前有效验收。客户确认依据仍由操作者填写，系统没有独立核验客户身份或确认事实。

只对“当前验收有效且基线、操作、支持三项工时齐全”的任务计算：

```text
可比任务实际总工时 = 实际操作工时 + 实施支持工时
工时变化率 =（可比任务基线总工时 - 可比任务实际总工时）/ 可比任务基线总工时
```

缺失值不补零；真实零投入可填 0；负数应保留，表示比原流程更耗时。操作工时应包括资料整理、写作、审核和全部返工；支持工时包括额外实施指导或故障协助，两者不得重复计算。机器等待与人员实际投入需要区分。

这是人工自报的可比交付任务工时统计，**不是完整项目 ROI、因果提效证明、自动计时、付款确认或完整交付成本核算**。失败/放弃任务虽然可见，其投入不进入上述通过任务的工时指标；初始化、销售、客户培训、失败尝试等成本仍需另外核算。最终功能和测试结论以本轮完成后的验收记录为准，本报告不预先宣称新增接口或页面已通过全部测试。

## 后续有价值的小切片

| 顺序 | 切片 | 可验收结果 | 何时值得做 |
| --- | --- | --- | --- |
| 1 | 本人复现和解释现有端到端流程 | 可从空测试库运行资料→生成→审核→交付→试点登记；能现场解释一处失败并改一个小需求 | 立即，与求职同步；无需等产品完善才投递 |
| 2 | 真实客户样本的固定评测集 | 从授权资料中选取重复问题/内容任务，标注依据、缺失和失败类型；保留测试集版本与坏例 | 客户愿意提供合法资料且能说明验收标准后 |
| 3 | 一个办公/低代码入口 | 优先选客户已经用的平台；实现认证、任务创建/查询、幂等与错误处理，留脱敏配置及端到端记录 | 客户已有明确重复流程；Dify/n8n 只选一个，不平行重建整个应用 |
| 4 | 交期、负责人和返工分类 | 每项内容有负责人与截止时间，退回原因可统计，旧版本仍可追溯 | 真实试点出现交接遗漏或反复返工后 |
| 5 | 生成质量与成本的对比 | 固定样本对比提示词/模型版本，人工盲评或复核；将供应商账单与调用量对账 | 检索与输入质量已稳定，有真实模型调用预算后 |
| 6 | 部署及恢复演练 | 在隔离环境完成部署、备份、恢复、模型故障定位和交接文档 | 首个真实试点交付前；公开服务须另行完成生产准备 |

## 商业落地的最小验证

先选择一个有重复工作和明确负责人可接触的团队，例如已有品牌资料的小型内容团队、产品服务商，或用户熟悉的跨境运营团队。首个范围是把批准资料加工成可审核的知识图文、产品答疑或案例内容包；客户仍负责事实确认、最终采用与发布。

访谈与试点需要确认四件事：谁每周做这件事、目前如何完成、哪些错误不能接受、谁有权验收和付款。能接触企业老板是调研入口，不等于已有客户。

试点按相同内容范围、相近难度和相同质量标准比较；不只选成功任务，记录退回、放弃、额外支持和初始化成本。先积累真实交付证据，再讨论固定范围服务费或持续服务。价格应由访谈、成本与付费实验确定，不能从招聘薪资或政策补贴倒推出客户愿意付多少钱。

两周试点的退出条件应在开始前约定：拿不到授权资料/负责人、不能确认验收标准、重复流程不足、返工和支持成本抵消收益、客户不愿继续投入时，暂停该场景并复盘。没有客户数据时，用明确标注的演示数据验证工程，不能写出虚构提效或收入。

## 求职表达边界

合适的项目介绍要回答“做了什么、自己负责什么、怎样验证、哪些尚未完成”。例如可先准备以下陈述，再按本人实际演示情况删改：

> 我在维护一个基于 Python/FastAPI、LangGraph 与中文 RAG 的内容交付工作台，流程包含资料检索、生成、人工审核和版本化交付。我能演示的模块是……，亲自修改和定位过的问题是……；当前验证范围是……，还没有真实客户付费/生产验收的部分是……。

不把 AI 辅助生成的代码量、依赖名称或历史测试数量当作本人熟练度。达到能运行、解释、复现、排错、修改并验证的模块，再写入简历能力描述。
