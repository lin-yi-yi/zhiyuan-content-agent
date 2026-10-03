# v0.8：可解释的混合检索与真实模型对照

更新时间：2026-09-19。新增能力是 **BM25 + 向量双路召回 + RRF 融合**，默认启动仍使用原来的 semantic。可在一次搜索、问答或评测请求中选择 hybrid，不会改其他请求的配置。

## 为什么加，实际做到哪一步

语义向量能处理表达差异，但错误编号、接口名等短词可能在余弦阈值下被误拒。BM25补充精确词项信号；中文采用字符二元组、三元组，英文术语、版本号和 `E-401`、`Idempotency-Key` 这类编号整体保留。无需额外下载分词模型。

检索流程：当前组织业务库 → workspace/知识库/资料有效状态过滤 → Qdrant 余弦召回 + 当前合格知识块 BM25 → 去重并以 RRF 排序 → 独立证据门控 → 引用式回答。

- BM25 在应用进程内用 Python 计算，参数 `k1=1.2,b=0.75`。词频和平均长度只取当前合格语料，撤销、过期、其他知识库的文档不进入 IDF 统计。
- 向量路继续使用真实 FastEmbed 中文 BGE 和本地 Qdrant。Qdrant 查询本身带 workspace、知识库、合格文档 ID 过滤，返回后继续核对 SQL 中的内容哈希。
- RRF 在应用层执行，采用从 1 开始的排名和 `sum(1/(60+rank))`，并以 chunk ID 稳定打破同分。它不是 Qdrant 默认 RRF 参数，也不是 cross-encoder 重排模型。
- 两路先分别召回最多 `max(top_k*5,40)` 个候选，再融合取 top-k；重复知识块只返回一次。
- 不增加依赖，不改变数据库表，semantic 与 hybrid 共享同一模型 fingerprint 和向量集合。已有语义索引无需重建；词项索引需先建立真实语义索引。
- 任一路所需语义模型或向量索引故障会明确报错，不把词项单路结果假装成混合结果。

## 分数不能混用

BM25 原始分数和余弦相似度量纲不同，不能相加或共用阈值。RRF 只读排名；结果 `score` 在 hybrid 模式下是 RRF 分数，常小于 0.04，不代表相关概率。

候选门槛 `min_score` 在 hybrid 中作用于原始余弦或词项覆盖率，**不作用于 RRF**。答案证据门控保持可解释的启发式：原始余弦达到现有 `RAG_SEMANTIC_MIN_SCORE`（默认 0.55），或 BM25 路命中的词项覆盖率达到 0.18。覆盖率还包含原有完整查询子串的 0.25 加分，并限制在 0～1。BM25 本身仍按频率、IDF和长度归一化排序。

这一 OR 门控会提高接纳率，也可能接纳只匹配少数词的错误证据。它不验证结论蕴含。后续应在独立领域标注集上选择门控或重排策略，不能把低误拒直接称为高正确率。

## API 合同

以下请求新增可选 `retrieval_mode: "lexical" | "semantic" | "hybrid" | null`，省略/null 继承环境配置：

- `POST /api/v04/rag/search`
- `POST /api/v04/rag/answer`
- `POST /api/knowledge/evaluate`
- `/api/v04/tools/execute` 的 `rag.search`、`rag.answer` 工具参数

示例：`{"query":"E-401","knowledge_base_id":1,"top_k":3,"retrieval_mode":"hybrid"}`。

搜索响应顶层有 `strategy: "hybrid_bm25_rrf"`、`retrieval`。每条结果的 `metadata.scores` 提供 `semantic_rank`、`semantic_score`、`bm25_rank`、`bm25_score`、`term_overlap`、`rrf_score`、`rrf_k`；未命中一路时该路排名/分数为 null。

`metadata.evidence_gate` 提供 `eligible`、`semantic_pass`、`lexical_pass`、两个门槛与 `rrf_used_for_evidence:false`。hybrid 的 `evidence_threshold` 为 null；调用者应使用 `select_evidence()`，不能继续把 `score` 与旧余弦阈值比较。

`rag.search` 原来固定返回 `local_hybrid_v1` 的错误标签已删除，现按实际模式返回 `lexical_overlap`、`semantic_cosine` 或 `hybrid_bm25_rrf`。

## 真实模型对照结果

执行 `.venv/bin/python scripts/evaluate_hybrid_retrieval.py`，在临时 SQLite + Qdrant 中索引两个固定语料集；不会读写正在运行的用户知识库，也不调用生成 API。首轮可能下载模型，之后复用 `.data/models` 缓存。

2026-09-19 实测：真实 `BAAI/bge-small-zh-v1.5` 模型，7 份人工合成中文文档，21 题（15 可回答、6 应拒答），top-k=3。未调整模型或阈值拟合数据。完整输出为 `.data/hybrid-retrieval-evaluation.json`，脚本可重新生成。

| 指标 | 词项演示 | 语义 | 混合 |
|---|---:|---:|---:|
| 候选 Recall@3 | 0.9333 | 1.0000 | 0.9333 |
| 首位候选文档召回 | 0.9333 | 0.9333 | 0.9333 |
| MRR | 0.9333 | 0.9667 | 0.9333 |
| 实际回答引用召回 | 0.8667 | 0.6667 | 0.9333 |
| 实际回答引用精度 | 1.0000 | 0.7143 | 0.7778 |
| 应拒答样本拒答比例 | 1.0000 | 0.8333 | 0.8333 |
| 可答样本接纳比例 | 0.8667 | 0.6667 | 0.9333 |
| 单题均值延迟（毫秒） | 2.55 | 8.69 | 9.77 |

**保留的坏例**：

- “没有相关资料时应该怎么回答？”：semantic 找到正确文档但门控误拒；hybrid 把正确文档挤出 top-3，同样误拒。
- “订单自动退款接口的签名算法是什么？”：资料只描述创建订单的幂等键，semantic 和 hybrid 都错误接纳了近似主题。引用 ID 合法不代表能回答问题。
- `E-401`、`E-403`、`Idempotency-Key` 等短编号/术语在 semantic 中被证据阈值误拒，hybrid 在本次样本中可采用词项证据。

结论：这是补齐工程能力、展示取舍的实验，不是“混合检索全面优于语义检索”的证据。保留默认语义模式，在界面高级选项中显式试用混合。合成语料过小，不能推断真实业务效果；延迟是单机串行测量，不是压力测试。

## 回归与面试实验

相关测试：`tests/test_hybrid_retrieval.py`、`test_rag_contracts.py`、`test_evidence_retrieval.py`、`test_evidence_vector_filter.py`、`test_saas_isolation.py`。合成向量合同测试使用真实临时 Qdrant，但不作为语义质量证明；上面的脚本才实际使用模型。

建议按顺序完成：

1. 阅读 `hybrid_retrieval.py` 的 `tokenize`、`bm25_rank`、`reciprocal_rank_fusion`，手算一条双路命中的 RRF 分数。
2. 在页面切换 semantic/hybrid 搜索 `E-401`，解释“候选有结果”与“回答愿意采用证据”的区别。
3. 复现两个坏例，增加独立真实领域标注样本，再提出改动。不要先调低阈值再用同一批样本报告成功。
4. 说明为何在每一路召回、IDF统计之前过滤权限；为什么只在结果末尾删掉其他租户记录还不够。
5. 说明如果文档数增加到百万级，应把 BM25 移到专门的索引服务、Qdrant 改独立服务，并测召回与并发；当前全语料扫描只适合本地小库。

## 权威依据

- [Qdrant Hybrid and Multi-Stage Queries](https://qdrant.tech/documentation/search/hybrid-queries/)：RRF 按排名融合异量纲候选；官方当前文档使用零起点排名，默认常数与本应用不同。本项目借鉴算法，不宣称使用 Qdrant 原生 sparse/BM25 融合。
- [Elastic Similarity module](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/index-modules-similarity.html)：BM25 的词频饱和参数 k1 与文档长度归一化参数 b。
- [Elastic Practical BM25: Algorithm and Variables](https://www.elastic.co/blog/practical-bm25-part-2-the-bm25-algorithm-and-its-variables)：IDF、频率和长度对得分的影响。
