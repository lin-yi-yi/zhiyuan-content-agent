# 单机容器升级与快照回退验收

`scripts/container_smoke.py` 为 GitHub Ubuntu runner 的现有 Linux Docker engine 提供隔离验收。宿主只需要 Python 标准库、Git 和 Docker CLI，不安装 Docker Desktop，也不依赖宿主的 Python 虚拟环境。

2026-10-04 已在 GitHub Ubuntu 24.04 / Docker 28.0.4 实际通过 v0.13.0 `9d850dd` → v0.14.0 `f58bd2e` 的 8 个业务检查点，清理无错误；[具体证据](validation/v014-operations-2026-10-04.md#github-与容器)。历史版本对保留在 [v0.13 记录](validation/v013-reliability-2026-10-04.md#github-与容器检查)和 [v0.12 记录](validation/v012-engineering-2026-10-04.md#容器远程验收)。本机没有 Docker，宿主逻辑测试与远程容器验收分开记录；后续版本仍以对应提交的 JSON 报告和 CI 为准。

## 执行

CI checkout 需要包含基线提交（例如 `fetch-depth: 0`）。工具不 fetch、不 push、不部署，也不切换当前工作区：

```bash
python scripts/container_smoke.py \
  --baseline-ref 9d850dd89d9b41c100aa68341bcd190d6d1f6fc2 \
  --output .data/validation/container-smoke/report.json
```

`--baseline-ref` 必填，解析成完整 commit；基线使用 `git archive` 的已提交源码。候选使用当前工作区中的 Git 已跟踪文件及非忽略的新源码。两者分别构建其真实 Dockerfile，记录 commit、构建上下文摘要、镜像 ID 以及运行时 `/api/health` 的版本。候选有未提交修改时，commit 本身不代表全部构建输入，必须同时查看上下文摘要。Docker 自身的构建缓存可复用相同依赖层，不跳过两个镜像的实际构建。

镜像构建允许从公共仓库拉取基础镜像和依赖，因而需要联网；应用运行期间使用 `--network none`，只允许容器内部 loopback。API 请求经 `docker exec` 在容器中访问 `127.0.0.1:8765`，不发布宿主端口。

## 实际检查

1. 基线镜像在空的临时 named volume 中启动。通过真实 API 创建仓库虚构工业资料、模拟核验、入库、品牌及 FAQ 任务；未批准时正式交付返回 409。
2. 脚本模拟批准，获取正式 Markdown；核对批准 hash、正文、文档和 chunk、资料版本、定位及来源；本地词项问答应仍能返回电压和流量。
3. 重启基线容器，确认批准状态、内容 hash 和引用保持不变。
4. 停止基线，使用基线镜像中的 `local_backup.py` 建立升级前快照。
5. 候选镜像挂同一原卷启动，确认能读取旧批准版本。随后改稿，确认旧批准失效、正式交付被拒，再模拟重审形成新 hash；重启后核对新版本仍存在。
6. 停止候选，把升级前快照恢复到另一个全新 volume 的新目录。使用基线镜像启动恢复目录，确认得到旧批准 hash、正文和引用；再次启动原候选卷，确认它仍是新 hash，恢复没有覆盖原卷。
7. 使用候选镜像启动恢复卷，再核对旧批准版本仍可读。记录各阶段签名、Markdown、构建及容器日志。

任一步异常返回非零，并写入失败报告，不能把缺 Docker 或失败当作跳过后通过。清理只删除带本次随机所有权标签的容器、named volumes 和镜像标签；不会执行 `prune` 或移除其他工作负载。Docker 构建缓存保留。若引擎中断导致清理失败，报告会列出具体资源名称，需要操作者核对后处理。

## 隔离和证据边界

- 不挂任何宿主业务路径，不读取 `.env`。构建输入仅来自 Dockerfile 所用源码目录，并排除所有层级的 `.env*`、数据库、缓存及私有日志。源码中的测试示例可以进入镜像，实际配置不会被转发。
- 运行环境固定 `local` 规则生成和 `lexical` 检索；所有模型 key 置空，关闭外部资料集成及 tracing。容器运行网络被 Docker 禁用，任务记录还会检查在线调用数为 0；token、供应商账单和人工成本不填成实测 0。
- `lexical` 不创建 Qdrant 索引。本轮归档应记录 `vector_index: absent`；**BGE 下载、语义/混合检索和容器内向量恢复没有被验证**。本地 Qdrant 格式的临时数据测试见 [本地备份说明](local-backup.md)。
- 回退验证的是**报告所列基线 → 候选这一版本对，恢复升级前快照后再运行基线**。当前改动未提出 schema 迁移；这不证明任意版本降级、让旧程序直接打开升级后数据库、迁移回滚、并发升级或零停机升级可用。
- 只验证单容器、单进程本地模式。Compose 模板、SaaS、反向代理、TLS、公网访问、线上资源容量、真实资料恢复和客户验收均未覆盖。
- 样本、核验和批准全部由脚本模拟，不代表来源真实、真实人审正确或客户接受。hash 验证版本一致性，不证明内容正确。

报告默认在忽略目录 `.data/validation/container-smoke/`。可把该目录作为 CI 合成验收 artifact 保存；报告中的通过范围和失败阶段比单独的 CI 绿色标志更具体。不要上传真实数据库、客户材料或操作密钥。

## 无 Docker 时可验证的部分

```bash
PYTHON_DOTENV_DISABLED=1 SAAS_MODE=false DATABASE_URL=sqlite:///:memory: \
RAG_RETRIEVAL_MODE=lexical DEFAULT_LLM_PROVIDER=local PYTHONPATH=backend \
.venv/bin/python -m pytest tests/test_container_smoke.py -q
```

这些测试检查构建输入过滤、无宿主挂载/外网的命令契约、资源清理边界、缺 Docker 失败报告，以及临时 SQLite 上的真实资料到批准交付和修改重审 API。它们不执行镜像构建或容器，也不能证明升级恢复已通过。
