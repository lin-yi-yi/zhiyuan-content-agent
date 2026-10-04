# 真实页面业务回归

浏览器用例位于 `frontend/e2e/industrial-faq.spec.ts`，使用固定版本的 `@playwright/test` 和独立 Chromium。它通过真实页面填写和点击验证业务流程；不调用 API 代替资料、品牌、任务、编辑或审核操作。

## 运行

先按 [SETUP](../SETUP.md) 安装 Python 与前端依赖。首次下载 Chromium：

```bash
cd frontend
npm ci
npx playwright install chromium
npm run test:e2e
```

Linux CI 安装浏览器及系统依赖时使用 `npx playwright install --with-deps chromium`。测试默认使用本机 8787 端口，已有服务占用时直接失败，不复用运行中的工作台。需要换端口时：

```bash
ZHIYUAN_E2E_PORT=8788 npm run test:e2e
```

`test:e2e` 会先做 TypeScript 检查并构建前端，再启动独立临时服务、执行浏览器用例并停止服务。该构建禁用 frontend `.env` 加载和继承的 `VITE_*` 注入。无需先启动 `start.sh`，也无需在线模型密钥。

## 验证什么

单条完整业务用例按以下顺序执行，每步都有页面或下载断言：

1. 确认空白临时数据库、local 生成与 lexical 检索。
2. 从「品牌与资料 → 更多 → 核验笔记」填写仓库内合成产品参数、版本、出处及原文定位，保存为待核验。
3. 通过页面确认已核验，再单独加入知识库；在知识资料页检查已入库资料。
4. 保存仅本地生成的品牌，从该品牌创建产品 FAQ，明确填写必需参数，等待人工审核。
5. 页面批准后点击正式交付下载；打开下载的 Markdown，核对品牌、资料版本、定位、chunk 引用与内容快照 hash。
6. 从原任务打开稿件，保存正文修改，返回原任务；确认页面回到待审、正式下载按钮消失，辅助接口检查返回 409。
7. 页面重新批准并再次下载；确认新正文已交付、hash 改变、引用仍保留，下载内容与当前批准稿一致。

API 只辅助检查隔离环境、正式交付拒绝状态及当前批准版本；没有用 API 预先造好业务成功状态。核验笔记使用 [工业 FAQ 合成集](../scripts/fixtures/industrial_faq_synthetic.json)，不代表真实产品、客户授权或客户验收。

## 隔离与产物

`scripts/browser_regression_server.py` 复用工业 FAQ 工装的配置隔离，在导入应用前禁用 dotenv，清空在线模型密钥、价格和外部追踪，强制所有任务模型为 local。数据库、向量目录和模型缓存均指向新建临时目录，正常结束后清理；不读取 `.data/demo.db`、客户资料或真实配置。

后端拒绝出站 socket 连接，浏览器拒绝测试服务以外的请求。用例同时检查没有外部模型调用记录、没有出站尝试及页面脚本错误。临时服务专用的 `/__e2e__/isolation` 不属于正式应用 API。

产物在 `output/playwright/`，只有该目录内生成物被局部 `.gitignore` 忽略，测试源码继续由 Git 管理：

- `report/`：HTML 报告。
- `results/`：两个实际下载的 Markdown、隔离检查附件；失败时保留截图和 trace。

失败时先看报告中具体步骤、页面截图和服务日志，修复后重新运行完整用例。产物仅包含合成材料，不应替换为用户资料进行回归。

## 证据边界

这条用例验证桌面 Chromium 下的本地资料、任务、审核和正式交付路径。它不替代后端权限与隔离测试，也未覆盖所有卡片排版、PNG/ZIP、SaaS 登录、手机布局、语义模型、在线供应商、客户使用或生产部署。原有检查继续使用 `./scripts/check.sh`；E2E 是额外的页面业务检查。

实际执行结果记录到对应维护验收文档，不在此处长期写死测试数量。测试配置参考 Playwright 官方的 [Web server](https://playwright.dev/docs/test-webserver) 和 [Downloads](https://playwright.dev/docs/downloads) 文档。
