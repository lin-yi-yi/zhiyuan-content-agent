# 本地资料库备份与恢复

`scripts/local_backup.py` 用于单机本地模式的 SQLite 和本地 Qdrant 索引。默认启动脚本的目录布局是 `.data/demo.db` 与 `.data/qdrant/`。SaaS 的控制库和组织库请继续使用 [SaaS 运维工具](saas-operations.md)，两种归档不互换。

命令始终要求明确选择来源。工具不读取 `.env`，不根据 `DATABASE_URL`、`RAG_VECTOR_PATH` 或其他环境变量猜测正在使用的数据库。如果你修改了启动配置，请使用对应的自定义路径，不能把默认 `.data` 的备份当成自定义库的备份。

## 停机备份

1. 等待任务结束，停止所有使用该数据库和索引的服务、脚本及 Python 进程。
2. 确认所选资料库、索引路径；准备仅自己可访问的归档目录。
3. 从项目根目录执行命令。`--offline-confirm` 表示你已确认所有写入者停止，不能覆盖锁检查。

默认目录布局：

```bash
mkdir -m 700 -p /absolute/path/to/private-backups
.venv/bin/python scripts/local_backup.py backup \
  --data-dir /absolute/path/to/ai-content-agent/.data \
  --output /absolute/path/to/private-backups/local-snapshot.zip \
  --offline-confirm
```

自定义数据库和索引可以位于不同目录，必须同时明确指定：

```bash
.venv/bin/python scripts/local_backup.py backup \
  --database /absolute/path/to/custom/content.sqlite \
  --vectors /absolute/path/to/custom-vectors \
  --output /absolute/path/to/private-backups/custom-snapshot.zip \
  --offline-confirm
```

如果确实只需数据库，在 `--data-dir` 或 `--database` 后加 `--without-vectors`，不再提供 `--vectors`。这会明确记录 `vector_index: "excluded"`，不会复制向量数据。对于 `--data-dir`，从未建立索引、`qdrant/` 尚不存在时仍能备份，结果记录 `"absent"`；显式 `--vectors` 指向不存在的目录则报错，避免把拼写错误当成“没有索引”。

成功后仅输出归档路径、文件数、字节数及 `vector_index` 状态，不输出资料、查询或数据库内容。目标归档不能已存在，也不能放进被备份的向量目录。

## 恢复到新目录

恢复前仍须停止写入。目标只能是不存在或全新的空目录；工具不会覆盖旧资料，也不会改写当前启动配置。

```bash
.venv/bin/python scripts/local_backup.py restore \
  --archive /absolute/path/to/private-backups/local-snapshot.zip \
  --destination /absolute/path/to/local-restored \
  --offline-confirm
```

归档统一恢复为 `local-restored/demo.db` 与可选 `local-restored/qdrant/`，即使原数据库文件叫 `content.sqlite`，恢复后的名称也为 `demo.db`。失败时只清理本次创建的临时目录，已有目标目录和其他进程创建的文件不被删除。

先保留原目录，用恢复目录和另一端口进行本机验证：

```bash
DATABASE_URL=sqlite:////absolute/path/to/local-restored/demo.db \
RAG_VECTOR_PATH=/absolute/path/to/local-restored/qdrant \
PORT=8773 ./scripts/start.sh
```

以上 SQLite URL 中 `sqlite:///` 后接以 `/` 开头的绝对路径，所以共有四个连续斜杠。没有向量索引的备份可先加 `RAG_RETRIEVAL_MODE=lexical` 启动检查；需要语义检索时，从恢复后的资料重建索引。已经包含索引时，应使用原嵌入模型、维度与缓存版本，不能把换模型后的结果当作原索引恢复成功。

验证 `/api/ready`、资料及任务数量、资料版本与引用、一个已审核任务的交付和一次查询。先确认恢复目录可用，再自行切换日常启动参数；不要删除原目录来完成切换。

## 保护与范围

- 在读取前获得与应用相同的 `<数据库名>.runtime.lock`，有索引时也持有 Qdrant `.lock`，直到归档写完。检测到活跃锁即拒绝；锁文件退出后保留，不要手动删除来强行备份。
- SQLite 使用原生 backup API，包含 WAL 中已提交的数据；不复制 `-wal`、`-shm`、`-journal` 侧文件。业务数据库与索引的整体一致性依赖停机，不能据此宣称支持在线跨库快照。
- 仅收集明确选择的业务库，以及当前本地 Qdrant 的 `meta.json`、`collection/<集合名>/storage.sqlite`。同时校验集合元数据与文件对应关系。不收集 `.env*`、常见密钥文件、模型权重/缓存与锁文件；未知向量文件格式会报错，不静默遗漏。不要将密钥放入业务数据库或索引内容：工具不检查或删改业务内容。
- 拒绝符号链接（包括所选路径的父目录）、路径穿越、归档重复成员、额外成员、异常类型、错误清单、损坏 SQLite 和哈希不匹配。请使用真实目录路径；例如某些系统的 `/tmp` 是别名，需换成其真实路径。
- 每文件记录 SHA-256、大小和类型。哈希可发现损坏，不证明归档来源可信；只恢复自己保管的归档。
- 归档与恢复文件权限为 `0600`，恢复目录为 `0700`。归档包含全部业务资料和索引，应当作为私有数据保管；工具不自动加密，也不上传到远程。
- 上限沿用 SaaS 工具：20,000 个文件、20 GiB 数据、10 MiB 清单。只支持本机文件 SQLite 和当前 Qdrant 本地格式，不支持远程数据库/向量服务、历史 dbm 索引、跨主机锁或多进程在线备份。硬链接别名和绕过应用的其他写入者仍需操作者排除。

## 自动验证范围

`tests/test_local_backup.py` 使用临时合成数据，验证真实 SQLite 与 Qdrant 备份恢复、恢复后的向量查询、业务库与索引库的 WAL 数据、应用锁和索引锁、私有权限、显式自定义路径以及损坏/恶意归档。不会读取日常 `.data` 或 `.env`，不会调用在线模型。

```bash
PYTHON_DOTENV_DISABLED=1 DATABASE_URL=sqlite:///:memory: SAAS_MODE=false \
RAG_RETRIEVAL_MODE=lexical DEFAULT_LLM_PROVIDER=local PYTHONPATH=backend \
.venv/bin/python -m pytest tests/test_local_backup.py tests/test_saas_operations.py -q
```

这些测试验证工具与合成索引，不替代你的实际业务资料恢复验收、Docker 验收或公网部署验收。
