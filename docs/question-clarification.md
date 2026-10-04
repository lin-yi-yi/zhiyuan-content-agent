# 从参数目录确认本次问题范围

v0.13.0 工程验证版的「内容任务 → 资料问答」已提供两步人工操作：先选择或填写本次所需的型号和参数，再对照原问题确认后提问。参数目录帮助用户选择资料里已经登记的品牌型号和参数，再补充本次问题需要、但资料可能没有的项目。它是人工澄清辅助，不解析自然语言问题，不判断问题是否列全，也不自动确认任何选项。

一般资料问答仍可使用原有接口。未传 `required_facts` 时，`answerability` 保持 `not_assessed`；找到相关摘录、生成回答或引用检查通过，都不代表完整问题已被证据支持。

## 目录接口

```http
GET /api/v04/rag/fact-catalog?workspace_id=1&knowledge_base_id=1&offset=0&limit=30
```

`workspace_id` 和 `knowledge_base_id` 可省略，采用现有默认工作区及知识库规则。指定知识库必须属于指定工作区；SaaS 模式还必须通过组织身份和成员权限校验，数字 ID 相同不允许访问另一组织的业务库。

`offset` 必须大于等于 0；`limit` 默认为 30，范围是 1–50。返回示例只使用合成资料：

```json
{
  "workspace_id": 1,
  "knowledge_base_id": 1,
  "items": [
    {
      "product_model": "合成星桥 XP-24",
      "parameter": "额定电压 / 直流输入",
      "evidence": [
        {
          "document_id": 1,
          "chunk_id": 1,
          "source_url": "https://example.com/synthetic-xp24#electrical",
          "locator": "合成规格第 1 节：电气参数",
          "version_label": "synthetic-v1"
        }
      ]
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 30,
  "has_more": false,
  "requirements_complete": false,
  "suggestion_method": "reviewed_evidence_metadata"
}
```

目录不返回参数值、笔记正文或引用摘录字段。出处仍是资料作者填写的 URL、定位和版本标签，不代表系统独立验证了外部来源的真实性。遇到导致当前状态无法判断的历史 JSON 结构损坏时，接口返回不含资料内容的 422 提示，不展示绕过核验的部分列表。

每次请求读取当前范围内的 SQL 文档和知识块，不调用生成模型、embedding、向量检索或远程来源抓取，也不依赖当前语义模型可用。它只采用仍已核验、已索引、授权允许使用、未过期且没有当前结构事实冲突的笔记；普通上传材料不作为参数选项。文档及笔记内容校验、块内容哈希、参数值实际位于引用摘录、摘录实际位于当前块、合法出处及定位均须通过。

按品牌型号和参数的 NFKC 规范化、去空白结果去重并排序，不忽略大小写，不做同义词或单位换算。一个选项可以保留多个真实出处。`total` 为去重后的选项数，分页发生在去重之后；同一份稳定数据的返回顺序固定。目录每次重新读取，分页期间资料变更可能改变页边界，不是冻结快照。当前实现遍历所选知识库的文档和块，分页只限制返回选项数，不声称已实现大库索引查询优化。

## 选择、补充、确认，再回答

当前页面的操作步骤：

1. 输入原问题，展开「从已核验资料选择参数」。目录支持翻页、刷新和查看登记出处，初始没有选中项。点击「加入」才会把对应型号和参数加入下面的清单；选择候选不会自动确认或发起问答。
2. 检查清单，每行是一组品牌型号和参数，最多 10 项，可以包含多个型号。用「添加手填参数」补充未登记的型号或参数，例如“售价”；可以编辑或删除已有行。相同规范化选项不会重复添加，半填行不能提交，空白行只是占位。页面不会把近似型号替换成用户输入的未知型号，也不会删除资料中没有的参数。
3. 对照仍显示的原问题，勾选「我已对照原问题，确认本次需要核对的型号和参数」，再点击「按确认参数提问」。页面将清单作为 `required_facts` 传给原来的 `POST /api/v04/rag/answer`，没有新增服务端“人工已核验问题完整性”的声明。
4. 修改问题、增加/编辑/删除参数行都会清除确认，需要重新勾选；切换知识库还会清空旧清单，重新打开目录时读取该库。旧查询或目录请求的异步结果不能覆盖新范围。

全部参数行留空时仍可点击「资料问答」，不要求勾选确认。这个入口保持原有一般问答能力，页面显示参数覆盖「未评估」，不是用一律拒答替代自由问答。

例如用户需要电压和售价，即使目录仅有电压，也应提交两项：

```json
{
  "query": "合成星桥 XP-24 的额定电压和售价是多少？",
  "knowledge_base_id": 1,
  "provider": "local",
  "required_facts": [
    {"product_model": "合成星桥 XP-24", "parameter": "额定电压 / 直流输入"},
    {"product_model": "合成星桥 XP-24", "parameter": "售价"}
  ]
}
```

回答接口继续执行现有精确事实契约：最多 10 项，每个字段去除首尾空白后 1–200 字；缺任意一项返回 `missing_structured_facts` 及 `missing_facts`。选择目录选项不是新的审批，也不冻结证据；回答时重新检索和校验，已撤销、过期、变更或冲突的来源不能因为曾出现在目录中而继续生效。目录列出知识库中登记的参数，不保证当前问题的检索结果一定采用该参数，也不保证当前语义索引配置可用。全部通过仅说明显式所列参数已核对，不说明机器已判断原问题完整性。

## 验证及范围

专项测试使用临时合成数据，覆盖分页归并、真实出处、资料失效、块及元数据完整性、工作区/知识库隔离、真实临时 SaaS 组织隔离、一般问答兼容和手填缺项。不调用在线模型，不读取用户业务库。

在仓库根目录运行以下命令；必须在导入应用或 pytest 收集测试前配置隔离环境。`configure_offline` 禁用项目 `.env`，清空模型密钥并关闭远程追踪，指定临时库、向量目录和 local/lexical 模式。测试内的 SaaS 用例另建临时组织库。

```bash
.venv/bin/python - <<'PY'
import sys, tempfile
sys.path.insert(0, 'scripts')
from evaluate_industrial_faq import configure_offline
with tempfile.TemporaryDirectory(prefix='fact-catalog-tests-') as folder:
    configure_offline(folder)
    sys.path.insert(0, 'backend')
    import pytest
    raise SystemExit(pytest.main([
        'tests/test_fact_catalog.py', 'tests/test_required_facts.py',
        'tests/test_fact_answer.py', 'tests/test_frozen_faq_evaluation.py', '-q',
    ]))
PY
```

前端校验和构建使用仓库已有依赖：

```bash
npm --prefix frontend test
npm --prefix frontend run build
```

单元检查不能替代真实页面操作；本轮页面验收记录与实际测试结果见 [v0.13.0 工程验证](validation/v013-reliability-2026-10-04.md)。

冻结评测 v1 的材料、题目、标签和评分规则保持不变。目录不改变自由问答的既有坏例，不能将人工选择或脚本填写正确契约后的结果冒充自然语言问答改进。交互层人工澄清效果需要另行记录用户操作和验收。
