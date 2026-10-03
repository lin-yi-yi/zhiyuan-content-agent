# 项目学习中心

学习中心围绕当前仓库实现组织课程，不根据依赖名称虚构已部署能力。

## 使用路径

1. 选择一个模块，查看原理和项目当前边界。
2. 进入「面试自测」，先自己回答，再展开参考答案、追问和常见误区。
3. 点击源码按钮，核对代码输入、输出与失败出口。
4. 进入「动手验证」，按解释 → 复现 → 故意改坏 → 排错 → 验收完成练习。

当前提供 7 个模块、29 道题、10 项练习和 26 个固定源码片段，覆盖前端、后端、RAG、工作流、SaaS、评测和信源扩展。技术栈总览默认折叠，日常操作只需选择模块和三个标签。

自己的回答、掌握程度与练习完成标记保存在当前浏览器 localStorage，按 local/saas 模式、用户 ID、组织 ID 区分。组织切换会重挂载学习页并清空旧页面内存；记录不上传服务器，不是跨设备同步或正式能力认证。浏览器存储被限制时显示无法持久化提示。

## 课程边界

- 本轮新增的混合检索是应用层 BM25 + 真实向量候选 + RRF，默认语义检索不变，没有交叉编码器重排模型。
- 文档导入支持文本 PDF 与 DOCX/MD/TXT，不代表 OCR 或多模态理解。
- LangGraph 执行受控条件工作流；项目用 SQL 保存步骤，没有原生 checkpointer，也不能宣称恰好一次恢复。
- Web 信源采用 REST；没有自建 MCP Server，也没有让内容工作流自主调用新闻 MCP。
- 几百项测试通过不能替代回答准确率和真实业务效果评测。

## 代码与接口

- `backend/app/services/learning_catalog.py`：课程、题目、练习和固定源码映射。
- `backend/app/api/routes/learning.py`：只读课程与源码片段接口。
- `frontend/src/pages/LearningCenterPage.tsx`：交互和源码阅读弹窗。
- `frontend/src/utils/learningProgress.ts`：作用域键、缓存校验与不可变更新。

接口为 `GET /api/learning/catalog` 与 `GET /api/learning/source/{source_id}`。源码只接受固定 ID；不接受文件路径、行号范围或任意读取请求。源码读取限制扩展名、大小、片段行数，并拒绝指向其他路径的符号链接；不读取配置、凭证、用户文档，也不调用模型。源码未随部署提供时明确返回不可用，课程定位失效时明确返回需要刷新。团队模式下接口继承 SaaS 中间件认证。

Docker 运行镜像除了原有后端与脚本，额外仅复制白名单涉及的 `frontend/src/api/client.ts` 和 `frontend/src/utils/draftNavigation.ts`，从前端构建阶段取得同版本文件。没有因此复制全部前端源码或本地配置。打包测试验证固定源码在独立目录可读取；这不替代真实 Docker 构建和部署验收。

## 验证

后端测试覆盖内容完整性、题目/练习唯一 ID、真实文件与定位、路径遍历拒绝、内外部符号链接拒绝、失效锚点、固定官方文档域名及能力边界文案。

前端测试覆盖 mode/user/org 进度隔离、键边界碰撞、刷新恢复、损坏缓存、答案长度限制、不可变更新与自评不自动升级。

SaaS 集成测试经过真实主应用与中间件，验证匿名拒绝、四种有效成员角色可读、被撤销成员拒绝、伪造组织拒绝、学习读取不占 AI 额度以及没有服务端学习进度写入口。

```bash
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_learning_center.py -q
cd frontend
node --test tests/learning-progress.test.mjs
```

官方参考链接已于 2026-09-19 核对：React、FastAPI、SQLAlchemy、Pydantic、Qdrant、LangGraph、OWASP、pytest 与 MCP 官方文档。具体代码能力仍以课程源码与本轮验收为准。
