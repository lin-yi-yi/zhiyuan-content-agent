# 工具问答的显式事实要求

`POST /api/v04/tools/execute` 的 `rag.answer` 工具接受与直接
`POST /api/v04/rag/answer` 相同的 `required_facts` 类型，并传入同一个问答服务。
工具白名单 `GET /api/v04/tools` 同时提供此参数的 JSON Schema。

v0.13.0 有一项调用方需要注意的兼容变更：过去 `rag.answer` 的参数模型没有声明 `required_facts`，Pydantic 默认会忽略这个字段及其他未知字段，导致调用方填写的事实要求未传到问答服务。现在 `required_facts` 已正式纳入工具 schema，按统一类型校验后传入服务；未知 `arguments` 字段改为严格拒绝，返回 HTTP 422。旧调用方若附带多余元数据或拼错字段，需按发布的 schema 修正，不能依赖过去的静默忽略。

```json
{
  "tool_name": "rag.answer",
  "workspace_id": 1,
  "knowledge_base_id": 1,
  "arguments": {
    "query": "合成星桥 XP-24 的额定电压和售价是多少？",
    "provider": "local",
    "retrieval_mode": "lexical",
    "required_facts": [
      {"product_model": "合成星桥 XP-24", "parameter": "额定电压 / 直流输入"},
      {"product_model": "合成星桥 XP-24", "parameter": "售价"}
    ]
  }
}
```

示例全部为合成数据；范围 ID 必须来自当前实例。型号和参数由调用方明确填写，
即使当前资料尚未提供售价也应列出，服务不会根据问题措辞自动推断完整要求。

- 最多 10 项；每项的 `product_model`、`parameter` 去除首尾空白后均为
  1–200 个字符，不接受额外字段。工具 `arguments` 本身也禁止未知字段，
  如误拼的 `required_fact` 或放错位置的 `knowledge_base_id`，返回 HTTP 422。
- 在当前 workspace 与 knowledge base 的有效检索摘录中按既有事实规则核对。
  缺项时在获取模型客户端前返回 `output.refused=true`、
  `refusal_reason=missing_structured_facts` 和具体 `missing_facts`。
  复合要求仅满足部分也会拒答；其他资料库的事实不能补足当前范围。
- 未传或传空列表时保持自由问答兼容行为，`answerability=not_assessed`。
  参数存在检查与 `answer_validation` 的适用范围仅为显式要求，不能据此声称
  已核验全部自由问答事实、来源真实性或产品的实际参数。
- 工具返回业务结果放在 `output`；直接接口返回同一业务结果。参数错误均为
  HTTP 422，但两条接口的错误详情格式不同。工具额外字段严格校验不意味着
  直接接口的所有顶层字段也采取相同策略。

`tests/test_rag_tool_facts.py` 使用临时 SQLite、合成核验笔记和 lexical 检索，
通过实际 API 验证导入、核验、索引、工具/直接接口结果一致、缺项先拒答、
错型号、字段与数量边界，以及 workspace/KB 范围隔离。测试阻断模型获取与
外部网络连接；它证明本地规则路径与参数传递，不证明在线模型质量或 SaaS
身份授权。当前本地模式的 workspace ID 是资料过滤范围，不是身份凭证。

依赖已安装后，在仓库根目录独立运行。先执行 `configure_offline`，再导入应用和收集测试，避免读取项目 `.env` 或复用日常数据库；配置只存在于此临时进程中。

```bash
.venv/bin/python - <<'PY'
import sys, tempfile
sys.path.insert(0, 'scripts')
from evaluate_industrial_faq import configure_offline
with tempfile.TemporaryDirectory(prefix='rag-tool-facts-tests-') as folder:
    configure_offline(folder)
    sys.path.insert(0, 'backend')
    import pytest
    raise SystemExit(pytest.main(['tests/test_rag_tool_facts.py', '-q']))
PY
```

需要页面辅助选择型号和参数时，见 [问题范围人工确认](question-clarification.md)。工程验证记录见 [v0.13.0 验证](validation/v013-reliability-2026-10-04.md)；这些本地合成验证不代表真实在线模型或客户业务已经验收。
