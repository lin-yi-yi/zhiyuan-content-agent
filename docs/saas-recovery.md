# 双组织 SaaS 与 Qdrant 备份恢复演练

`scripts/saas_recovery_smoke.py` 使用临时合成账号和资料，验证控制库、两个组织业务库及真实 Qdrant 本地索引能一起恢复。验收依据是恢复后的登录、资料哈希、检索、角色和用量，不只看归档成功或 HTTP 200。

这是同一候选源码或镜像的停机恢复演练。执行说明不代表 Docker 已通过；本机与 Docker 的结果分别以对应提交、输入摘要和实际 JSON 报告为准。真实数据的停机备份操作见 [团队运维](saas-operations.md)，学习步骤见 [开发实验](learning-labs.md)。

## 运行方式

在项目根目录运行。工具不接受现有业务数据路径，不连接日常服务；它自己建立临时目录或 Docker named volumes、启动单进程夹具并在结束时清理所属资源。不需要填写账号、模型密钥或 `.env`。

本机模式使用项目已安装依赖的 Python 3.12+ 虚拟环境，包括 `qdrant-client`：

```bash
.venv/bin/python scripts/saas_recovery_smoke.py \
  --runtime local \
  --output .data/validation/saas-recovery/report.json
```

Docker 模式需要可用的 Docker engine、Docker CLI、Git 和 Python 3.12+；宿主驱动只使用标准库，不要求宿主安装应用依赖：

```bash
python scripts/saas_recovery_smoke.py \
  --runtime docker \
  --output .data/validation/saas-recovery-docker/report.json
```

默认 `--runtime docker`，默认输出为 `.data/validation/saas-recovery/report.json`。分开输出目录便于保留两种运行证据；相同输出路径再次运行会更新报告和相关日志。不要把缺 Docker 或缺依赖登记为通过。

Docker 构建会联网下载基础镜像和依赖。应用和运维容器运行时使用 `--network none`，不发布宿主端口，不挂载宿主业务目录；HTTP 探针经 `docker exec` 访问容器内部回环地址。本机夹具只监听随机回环端口，禁用代理和外部集成，并拦截 Python socket 连接、DNS 与数据报发送；这是进程内防护，**不是操作系统防火墙**。

## 九个检查点

| 报告中的阶段 | 通过条件 |
| --- | --- |
| `live_empty_store_backup_refused` | 服务已启动但尚未打开租户向量库时，备份因真实服务锁被拒；其他原因失败不能代替这项通过 |
| `seeded_two_organizations` | 经真实注册与邀请建立两个 owner 和组织 A 的 viewer；各自入库不同标记资料，数字文档和 chunk ID 故意相同；形成内容、索引、权限和用量基线 |
| `source_updated_after_snapshot` | 停服建立快照，再启动原卷追加一份资料；签名必须与快照基线不同 |
| `corrupt_archive_refused` | 修改归档成员但保留旧哈希，恢复必须因哈希不符失败，且不发布恢复目标 |
| `restored_files_match_manifest` | 正常归档恢复到另一个隔离目标，逐文件核对清单哈希，包含两个组织的真实向量文件 |
| `existing_destination_refused` | 再次向已有数据的恢复目录恢复必须被拒，目标文件保持不变；工具只允许不存在或全新的空目录 |
| `original_files_unchanged` | 原卷已停机时，比较恢复操作前后的文件指纹，确认没有覆盖原数据 |
| `restored_login_queries_roles_usage` | 丢弃客户端会话后先验证匿名 401，再重新登录；两组织 semantic/hybrid 检索只返回自己的内容及相符的引用 ID、哈希；伪造组织头、缺 CSRF 和 viewer 写入均返回 403；资料、索引、角色和用量签名与快照相同 |
| `original_retains_new_document` | 再次启动原卷，核对快照后新增资料及完整更新签名仍存在 |

每个组织在建立基线时通过一次 `local` 模型 API 消耗一笔请求额度。恢复核验使用不消耗 AI 请求额度的 `/api/v04/rag/search`，并比较核验前后的用量。这里验证请求账本，不测真实模型费用、token 或资金退款。

## 如何判断结果

场景成功返回退出码 0，报告 `status` 为 `passed`；场景或清理失败返回 2。检查九个 `stages`，以及 `source_isolation`、`restored_isolation` 中两个租户向量存储、外部调用记录和连接尝试。只看到部分阶段通过，不能认定恢复成功。

公开报告只保留允许的签名与运行元数据，不保存密码、Cookie、CSRF 或邀请 token；认证响应只在内存中处理。临时备份里的控制库仍包含合成账号的认证状态，不能将这套报告规则理解为真实数据库可以公开分享。运行输出默认位于忽略目录，分享前仍应检查材料范围。

失败时先保留报告，按具体阶段检查依赖、服务启动、锁、文件哈希或恢复后的 API。清理只处理本次拥有的进程、临时目录或带所有权标识的容器资源，不执行全局 `prune`。报告出现清理错误时，按其中的资源信息核对残留；不要删除日常数据目录或锁文件来强行通过。修复后重新运行整套演练，不能手改 `passed` 字段。

## 服务锁与证据边界

SaaS 数据目录的 `.service.lock` 由生产应用 `app.main` 的 lifespan 持有：先取得锁，再初始化控制库，退出时关闭向量存储与数据库后释放。标准启动脚本、直接 Uvicorn 和 Docker 启动都经过这一路径；不要关闭 lifespan。备份工具使用相同锁，所以尚未创建任何 Qdrant 索引的活跃服务也会阻止备份。锁文件保留在目录内，停服后无需删除。

这是同一文件系统上协作进程的建议锁。它不阻止绕过协议的外部文件写入者，不提供多机协调、热备份或多 worker 运行支持。真实备份仍需停止所有写入者并显式确认停机。本演练按顺序正常停服，不证明断电、强杀中断或任意并发写入下的恢复。

- **真实存储、合成向量**：认证、角色、请求额度、SQLite 与 Qdrant 文件操作走真实实现；embedding 使用固定三维常量，元数据明确为 `synthetic` / `zhiyuan-recovery-synthetic-3d-v1`。semantic/hybrid 路径通过只能证明持久化与范围隔离，不能证明 BGE 下载、缓存可用性、语义排序或答案质量。
- **恢复会话不等于撤销会话**：控制库中的会话哈希随备份恢复。驱动丢弃客户端 Cookie 并重新登录，不声称恢复后旧会话全部失效。
- **同一候选恢复**：不覆盖跨版本升级、降级或迁移回滚；[原有容器验收](container-acceptance.md)单独记录其版本对与词项流程，不可混用两套证据。清单 hash 证明文件完整性，不是备份来源的数字签名。
- **环境验收有限**：不证明 Compose、真实域名、TLS、公网入口、SaaS 浏览器流程、容量与并发、真实客户资料恢复或生产可用性。本机通过不能替代 Docker 执行，Docker 通过也不能替代目标部署和客户验收。

实现入口为 `scripts/saas_recovery_smoke.py`（编排）、`scripts/saas_recovery_fixture.py`（隔离夹具）、`scripts/saas_recovery_scenario.py`（HTTP 业务核验）、`scripts/saas_backup.py`（停机归档）。自动测试使用临时数据；完整项目回归仍使用 `./scripts/check.sh`，实跑恢复演练另行保存报告。
