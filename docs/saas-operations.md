# SaaS 单机试点运维

本版本是单主机、单应用进程的 SaaS 试点。组织身份及用量在独立 `control.db`，业务资料在各组织独立 SQLite 和向量目录。以下工具不迁移或覆盖原本的 `.data/demo.db`。Compose 是部署模板，不能把配置存在或本地测试通过当作 Docker、公网或生产验收通过。

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

AI 额度统计 HTTP 受理尝试，包含执行中的预留，不能当作 token 或货币费用。HTTP 失败会退回；异步任务受理成功后，后续执行失败不自动退回。进程崩溃可能留下 `reserved`；只读命令返回请求哈希摘要和时间，不输出内部结算 token。应先核对请求与任务记录，确认没有仍在执行或已成功受理的任务，再由运营人员处理。当前没有按超时自动退款或批量清账功能。

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
