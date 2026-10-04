# SaaS 单机试点运维

本文按 v0.14.0 工程验证版维护，适用范围仍是单主机、单应用进程的 SaaS 试点。组织身份及用量在独立 `control.db`，业务资料在各组织独立 SQLite 和向量目录。以下工具不迁移或覆盖原本的 `.data/demo.db`。Compose 是部署模板，不能把配置存在或本地测试通过当作 Docker、公网或生产验收通过；本轮本地浏览器与容器结果见 [验收记录](validation/v014-operations-2026-10-04.md)，没有新增 SaaS 浏览器验收结论。

## 本机启动

先完成已有依赖安装，并在 `frontend` 运行 `npm run build`。启动工具会检查 Python 环境、依赖及 `frontend/dist/index.html`，不会自动安装依赖或构建前端。

在仓库根目录创建独立 `.env.saas`。不要复制或提交真实密钥到 Git：

```dotenv
SAAS_MODE=true
SAAS_PUBLIC_ORIGIN=http://127.0.0.1:8766
SAAS_SECURE_COOKIE=false
SAAS_ALLOW_REGISTRATION=true
SAAS_PORT=8766
GITHUB_ENABLED=true
AIHOT_ENABLED=false
AIHOT_COMMERCIAL_AUTHORIZED=false
RAG_RETRIEVAL_MODE=semantic
# SAAS_DATA_DIR=/absolute/path/to/isolated/saas-data
# DEEPSEEK_API_KEY=由运营方在受保护环境配置
# QWEN_API_KEY=由运营方在受保护环境配置
# DOUBAO_API_KEY=由运营方在受保护环境配置
# KIMI_API_KEY=由运营方在受保护环境配置
```

```bash
chmod 600 .env.saas
./scripts/start_saas.sh
```

默认访问 `http://127.0.0.1:8766`，数据在仓库 `.data/saas`。脚本只监听 loopback，固定 `--workers 1`，运行期间持有 `.service.lock`。环境变量优先于 `.env.saas`；可用 `SAAS_ENV_FILE` 指向另一个专用文件。脚本不会读取旧演示 `.env`，也会把遗留数据库 URL 固定到 SaaS 目录中的占位路径，真实业务仍使用租户库。

首次创建组织时注册用户成为该组织所有者。模型卡的“已配置”仅表示运营方凭证存在，未代表网络、余额或输出质量已经验证。需要由组织所有者启用模型并选择默认项；本地模型仅用于确定性流程演示。

AIHOT 必须同时满足运营方确认商业授权、`AIHOT_ENABLED=true`、`AIHOT_COMMERCIAL_AUTHORIZED=true` 及组织启用。设置环境变量是运营方对已获得授权的声明，工具不会代办或验证合同。GitHub 只允许读取固定仓库发布元数据。飞书和企业微信仍是规划项。

## 套餐与预留用量核对

命令需要本机数据目录访问权限；不会通过用户 API 提升套餐，也没有真实支付、自动扣费或发票功能。

```bash
.venv/bin/python scripts/saas_admin.py list-orgs
.venv/bin/python scripts/saas_admin.py set-plan --organization <组织ID> --plan team --ai-requests 1000 --documents 1000 --members 10
.venv/bin/python scripts/saas_admin.py set-plan --organization <组织ID> --plan trial
.venv/bin/python scripts/saas_admin.py reserved-usage --organization <组织ID>
```

自定义数据目录应显式传 `--data-dir /absolute/path`，放在子命令之前。团队套餐必须由运营人员明确给出三个额度；以上数字只是命令示例，不是定价或容量承诺。试用套餐固定为每 UTC 自然月 100 次 AI 请求、200 份已索引文档、3 个活跃成员。降低套餐不会删除既有成员或资料，超限后应先核对使用量。

AI 额度统计业务 HTTP 受理尝试，包含执行中的预留，不能当作 token 或货币费用。首次明确业务响应小于 400 为 `accepted`，尝试确认使用；只有业务明确拒绝（HTTP 状态大于等于 400）的 `rejected` 才尝试退回次数额度。尚无明确业务响应时取消、未获得业务响应，或内部错误边界合成兜底 500 时，`usage_outcome=unknown`，保留 `reserved`，不能将最终 HTTP 失败直接等同未受理。

每次预留最多自动尝试一次结算；提交后回执仍可能失败，因此 `usage_finalization_failed` 只说明结算未确认，不保证控制库仍为 `reserved`。后续审计、网络发送或已受理异步任务执行失败，不逆转 `accepted`，也不触发反向退回。仍为 `reserved` 的记录继续占用额度。只读命令返回请求哈希摘要和时间，不输出内部结算 token；应结合可信请求 ID、账本、任务及已提交副作用查清状态，未知时不改动。当前没有按超时自动退款、自动重试结算或批量清账功能。故障定位与处置见 [结算失败与未知受理](usage-finalization-failures.md)。

查清单条预留后，可用新增 `reconcile-usage` 默认预览、停机执行并保留原子审计。确认已受理时转为已使用，确认未受理时退回尝试额度；未知仍保留。完整命令、幂等重试和操作者身份边界见 [预留用量核对](usage-reconciliation.md)。

## 磁盘预检与请求排错

需要批量导入、生成或备份时，可先检查实际团队目录所在文件系统。目录须已存在，下面路径和阈值须按部署替换：

```bash
.venv/bin/python scripts/ops_health.py \
  --directory saas=/absolute/path/to/saas-data \
  --min-free-bytes 1073741824 --min-free-percent 5
```

工具只返回固定字段 JSON：退出码 0 表示所选目录达到阈值，1 表示低空间，2 表示未知或参数错误，未知优先。它不读取组织库或 `.env`、不创建目录或锁；同盘角色的余量不能相加。低空间时先暂停发起新批量操作，人工核查占用、释放或迁移空间，再以原阈值复检。不得依据预检自动删除数据、终止服务或退回用量。详见 [磁盘预检与处置](ops-health.md)。

这是一次性读数，未接入 `/health`、`/ready` 或启动门禁，不检查容器配额，也没有持续告警或真实 ENOSPC 写入恢复验收。容器及网络挂载的读数需按实际文件系统理解，不保证挂载身份、后续可写性或任意路径的零网络 I/O。

资料问答页的 HTTP 失败可显示 [可复制排错编号](request-troubleshooting.md)，仅复制当前响应头中的合法请求 ID；网络失败或无合法编号时不补造。用编号和发生时间查询私有诊断日志，先核对受理状态再决定是否重试，不发送正文、Cookie 或整份数据库。该入口不自动上传日志或通知维护者；组织切换后的旧错误隔离有请求层测试，不能据此声称 SaaS 浏览器验收已完成。

## 停机备份与恢复

先停止 SaaS 进程或容器，确认后台任务已结束。工具要求显式 `--offline-confirm`，并拒绝活跃的服务锁及向量索引锁。通过其他方式启动而没有服务锁的进程无法被完全检测；该参数表示操作者已确认所有写入者停止。单个 SQLite 快照不等于跨数据库、向量索引整体一致性，不能在线备份后宣称具有此保证。

```bash
.venv/bin/python scripts/saas_backup.py backup --data-dir /absolute/path/to/saas-data --output /absolute/path/to/backups/saas-2026-09-19.zip --offline-confirm
.venv/bin/python scripts/saas_backup.py restore --archive /absolute/path/to/backups/saas-2026-09-19.zip --destination /absolute/path/to/saas-restored --offline-confirm
```

备份目录必须已经存在，备份文件必须在数据目录之外且不得已存在。恢复目标只能不存在或为空目录。工具从不覆盖已有 SaaS 数据，也不自动切换正在使用的数据目录。

收集范围为 `control.db`、`tenants/<32位组织ID>/content.db`、各组织 `vectors/`。SQLite 使用一致性备份 API，包含 WAL 中已提交数据；向量文件在停机状态复制。不包含 `.env*`、模型权重缓存、运行锁或旧演示库。控制库含密码哈希及会话记录，业务库含用户资料，向量索引同样应视为私有数据。备份文件权限为仅当前用户读写；请存入受访问控制的备份位置。

清单记录逐文件 SHA-256、大小和类型。恢复拒绝越界路径、符号链接、重复成员、未列入清单的文件和损坏 SQLite，并核对全部文件哈希。哈希用于发现损坏，不是备份来源的数字签名。默认工具上限为 20,000 个文件和 20 GiB；超过时应升级备份方案，不应删改清单绕过限制。

恢复到新目录后，先用 `saas_admin.py --data-dir <新目录> list-orgs` 核对组织与套餐，再用新 `SAAS_DATA_DIR` 在本机端口启动。分别登录至少两个组织检查资料隔离、文档数量、查询引用、成员权限和套餐用量。恢复检查通过后才切换正式入口。向量模型缓存不在备份中，需要使用原版本模型恢复缓存；不要把更换嵌入模型后的检索当作同一索引验收。

## HTTPS 与 Compose 模板

公网入口必须使用真实域名和 TLS，例如把 `.env.saas` 改为 `SAAS_PUBLIC_ORIGIN=https://your-actual-domain`、`SAAS_SECURE_COOKIE=true`。这不是可直接使用的生产域名，须替换为已配置 DNS 和证书的域名。由 Nginx、Caddy 或已有网关反向代理至本机 `127.0.0.1:8766`；保留真实 Origin，限制可信代理，按实际入口设置请求大小及超时。仅在本机 HTTP 调试时关闭 Secure Cookie。

```bash
docker compose -f compose.saas.yaml config
docker compose -f compose.saas.yaml up --build -d
docker compose -f compose.saas.yaml logs --tail=100 saas
docker compose -f compose.saas.yaml stop saas
```

模板使用独立 `saas-data` 卷，宿主端口仅绑定 `127.0.0.1:8766`；容器内部监听 `0.0.0.0` 便于宿主代理访问。它不会复用旧 `content-data` 卷。不要把 Windows/macOS 宿主数据路径直接理解为容器内路径；备份前需要停止容器，并用明确的卷挂载路径操作数据。不要执行会删除数据卷的清理命令来“重启”。

模板健康检查只验证 `/api/health` 存活，并传递配置域名的 Host；它不替代登录后的 `/api/ready` 或实际业务验收。运维脚本已打包进镜像，容器内使用 `python scripts/saas_admin.py`，不使用宿主的 `.venv` 路径。备份卷可在停服后由临时运维容器读取，例如：

```bash
mkdir -p backups
docker compose -f compose.saas.yaml stop saas
docker compose -f compose.saas.yaml run --rm --no-deps --entrypoint python -v "$PWD/backups:/backups" saas scripts/saas_backup.py backup --data-dir /app/.data/saas --output /backups/saas-2026-09-19.zip --offline-confirm
```

宿主备份目录须仅授权运维人员及镜像运行用户写入，不能通过开放所有人写权限解决权限问题。恢复同样使用临时运维容器，目标指定新的目录，例如 `/app/.data/saas-restored`；验证后再显式修改模板中的 `SAAS_DATA_DIR`，不要覆盖旧目录。上述 Docker 命令需要在实际安装 Docker 的环境另行运行验证。

当前 SQLite、本地 Qdrant、任务状态和登录限流有单进程前提；不得增加 Uvicorn worker 或多副本共享同一目录。源码对每个实例缓存的组织存储数量有上限，这不是吞吐量、可用性或计费保障。正式开放前须在目标机器实测登录并发、长任务、索引内存、磁盘容量、备份恢复和外部模型失败场景；至少验证 HTTPS Cookie、CSRF、跨组织隔离、配额边界、来源商业授权和故障后的预留核对。需要水平扩容时应先迁移共享队列、数据库、向量服务与限流状态。

## 实现参考

会话与 CSRF 控制参考 [OWASP Session Management](https://cheatsheetseries.owasp.org/Session_Management_Cheat_Sheet.html)、[OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)；密码 KDF 参数参考 [OWASP Password Storage](https://cheatsheetseries.owasp.org/Password_Storage_Cheat_Sheet.html)。组织上下文入口使用纯 ASGI 中间件，避免 [Starlette 文档](https://www.starlette.io/middleware/)列出的 BaseHTTPMiddleware 上下文传播限制。参考这些指南不等于获得安全认证。
