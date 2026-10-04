# R05 冻结合成 FAQ 评测

这是一组**助手编写、尚待逐题人工复核、非独立盲测**的合成材料与问题。冻结文件用于复现和防止悄悄改题，不能证明材料真实、标签正确、开发者未见过题目或客户业务效果。评测不调用在线生成模型。

## 题集和分组

`scripts/fixtures/faq_frozen_v1/` 包含 14 份虚构资料、20 个问题：

| 文件 | 作用 |
| --- | --- |
| `corpus.json` | 型号、版本、参数、原文摘录与定位；按场景族划分资料范围 |
| `cases.json` | 固定问题、开发/保留组、预期文档、必须保留的事实短语、参数契约、缺项与拒答原因 |
| `manifest.json` | 文件 SHA-256、配置及其哈希、冻结时间、基线提交、来源、分组政策与证据边界 |

`development` 和 `reserved` 各有 10 题、5 个场景族，分别覆盖同义表达、跨型号、复合缺项、否定和冲突。同一场景族和产品资料不能跨组，规范化后的相同问题也不能重复。每个场景族使用独立知识库，因此这是小范围资料问答实验，不是大规模混合语料检索能力测试。

每类包含不同问题表达或是否提供显式参数契约的变体。自由问题不会自动获得 `required_facts`；显式参数仍按已登记名称匹配。同义问法的测试不代表系统支持参数别名映射，否定短语被保留也不等于模型理解了否定逻辑。

保留组的含义是“计划不用于调整这一冻结配置”。题集由了解已有实现和坏例的开发助手编写，文件公开可读，程序无法认证真实调参历史；报告固定标明 `unseen_status: not_claimed`、`tuning_exclusion_independently_verified: false`，不会将新生成的问题称为真正未见过。若根据保留组结果改动算法、提示词或阈值，后续应将这批题作为开发反馈，并另外规划下一轮保留样本。

旧的工业 FAQ 10 个自由问题、4 个参数契约问题，以及混合检索 21 题仍属于既有开发样本。本轮不改动这些文件或重新给旧题贴上“保留/盲测”标签。

## 运行

从项目根目录运行。默认只执行开发组、词项检索：

```bash
.venv/bin/python scripts/evaluate_frozen_faq.py
.venv/bin/python scripts/evaluate_frozen_faq.py --split reserved
```

报告默认保存到 `.data/evaluations/frozen-faq-<split>-<mode>-<时间>-<随机标识>.json`，也可通过 `--output /absolute/path/result.json` 指定新文件。已有报告拒绝覆盖；报告不允许写进冻结题集目录。退出码 0 表示评测流程完成，**不表示没有质量坏例**。退出码 2 表示未完成或输入/输出条件不满足。

语义和混合模式必须显式选择已有缓存：

```bash
.venv/bin/python scripts/evaluate_frozen_faq.py --mode semantic --cache-dir .data/models
.venv/bin/python scripts/evaluate_frozen_faq.py --mode hybrid --cache-dir .data/models
.venv/bin/python scripts/evaluate_frozen_faq.py --split reserved --mode hybrid --cache-dir .data/models
```

固定使用真实 `BAAI/bge-small-zh-v1.5`、FastEmbed 和临时 Qdrant，模型为 512 维、2 线程。只复制所选缓存中对应 BGE 的 Hugging Face 或旧 FastEmbed 模型目录到临时环境，源缓存只读取；库可能创建的缓存临时文件仅落在副本。需要额外临时磁盘空间。运行设置 `HF_HUB_OFFLINE=1` 并禁止 socket 出站连接，缺缓存或推理失败时明确未完成，不下载、不伪造向量、不降级为词项检索。完成报告记录实际模型文件 SHA、FastEmbed 版本、实际维度和线程数。

CLI 在导入应用前禁用 `.env`、在线信源、模型凭证及外部追踪，并覆盖继承的数据库、索引、供应商和检索配置。资料导入、核验、索引与问答使用实际 API，数据库及向量库都在临时目录；退出后清除。脚本模拟核验仅用于建立状态，不等于用户人工审定标签。

冲突场景先确认正常核验接口拒绝矛盾资料，再在临时库注入一条“历史已核验、尚未索引”的冲突记录，检查当前来源会被排除。报告 `fault_injections` 明确列出注入记录，不将这种状态描述为正常可核验入库。

## 冻结与修改规则

脚本登记整个 manifest 的 SHA；manifest 再登记语料、问题和配置 SHA。改文件、标签或阈值后只重算清单，仍不能冒充原冻结版本。`--dataset-dir` 仅用于读取相同字节的包副本。

冻结配置固定 top-k=3、候选门槛 0.08、词项采用门槛 0.18、语义采用门槛 0.55、BM25 参数 1.2/0.75、RRF 常数 60、切块 420/70。CLI 不提供阈值调参选项。运行时检查应用配置、融合参数及当前代码中的候选/切块/词项门槛；语义运行另外核对实际维度与线程数。实现文件 SHA、Git 提交及工作区是否有改动也进入报告；无法读取 Git 状态时保留 null。

确有标签错误时，应记录修订原因、人工复核状态及新版本，重新登记冻结包；不能删除坏例、调整题目措辞后继续使用 v1 成绩。来源/标签未经人工复核这一状态不会因为脚本全绿而升级。冻结哈希是版本一致性检查，不是不可篡改审计或独立第三方认证。

## 如何读报告

- `completed` 表示本次是否完整运行；失败报告不补写成功汇总，也不借用以前报告。
- `configuration_sha256` 是固定配置哈希；`runtime_configuration_sha256` 还包含本次检索模式和索引 fingerprint。不同策略应分别运行与比较，不能把它们混成一个总体分数。
- `cases` 保留全部所选问题、预设标签、实际答案、候选、引用、缺项、拒答原因、延迟和坏例原因；`bad_case_ids` 不删减失败样本。
- `summary` 与 `by_category` 保留分子、分母和比值；没有适用样本时为 null。候选文档召回与实际回答引用召回分开统计，误拒不会因为候选正确而被算作答案成功。
- `labelled_phrase_support_rate` 只看预设短语是否同时出现在回答和被引摘录。它不验证来源真伪、完整语义、否定推理、额外断言或完整答案准确率。
- `false_accept_rate` 的分母仅为预设应拒答题，`false_refusal_rate` 的分母仅为预设可答题。人工标签尚未审定，当前数值均是基于预设标签的探索结果。
- 所有模式均使用本地原文摘录回答；真实嵌入推理不等于真实生成模型质量。账单、token、设备与人工成本未测则为 null，外部生成请求数为 0。
- 延迟是单机串行、模型已加载后的逐题 API 耗时，未计入缓存复制、模型启动或资料导入，不是并发性能或生产 SLO。

首次 development 词项运行完成 10 题：5 个预设可答题均接纳；5 个预设应拒答题中有 2 个错误接纳，分别为 `d-cross-model-2` 和 `d-compound-2`。它们展示“相关型号原文不能回答未登记型号”以及“有部分依据不能回答完整复合问题”的现有边界；未据结果修改题目或阈值。这不是经过人工复核的业务正确率。

## 专项验证

```bash
PYTHON_DOTENV_DISABLED=1 DATABASE_URL=sqlite:///:memory: SAAS_MODE=false \
RAG_RETRIEVAL_MODE=lexical DEFAULT_LLM_PROVIDER=local PYTHONPATH=backend \
.venv/bin/python -m pytest tests/test_frozen_faq_evaluation.py -q
```

专项覆盖冻结哈希、防重算替换、场景族/产品/题目重叠、标签声明、指标分母与 null、两个 split 的实际词项 CLI、污染父环境隔离、缓存缺失拒绝、源缓存副本边界以及报告不覆盖。指标单元测试里的合成响应只验证计算逻辑，不能当作语义模型验收。
