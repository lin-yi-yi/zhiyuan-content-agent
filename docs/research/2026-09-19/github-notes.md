# 知源可以借鉴的 GitHub SaaS / AI 项目

查询日：2026-09-19（Asia/Shanghai）。范围：8 个项目的官方 GitHub 仓库、默认分支近期提交、README 与许可证原文；没有安装、运行或复制这些项目的代码。近期提交仅说明有人维护，不代表安全、可靠或具备商业交付能力。以下是研发选型记录，不是法律保证。

知源目前适合继续保留 React / FastAPI / RAG 主体，做成可解释、可验证的求职作品，再寻找试点。对没有客户渠道、由一个人交付的当前阶段，整套 fork 一个大型 AI 平台，会把时间消耗在平台部署、升级和许可证边界上。优先借鉴组织权限、运行追踪、用量记录与证据展示。

## 仓库对照

日期为核查到的默认分支提交日期（UTC）；链接固定到当时的提交。功能列来自官方 README，未将官方宣传当作实际运行证明。

| 项目 | 官方定位与值得借鉴的模块 | 近期维护证据 | 许可证与 SaaS / 白标边界 | 对知源的选择 |
| --- | --- | --- | --- | --- |
| [Dify](https://github.com/langgenius/dify) | 可视化 AI 工作流、RAG、应用运行记录；借鉴步骤状态、输入输出与失败原因展示。 | [2026-09-18 提交](https://github.com/langgenius/dify/commit/5568aa0ea57d3a2c1cec03bd747b975494f094e3)；[1.17.1 发布](https://github.com/langgenius/dify/releases/tag/1.17.1)为 09-10。 | [Dify Open Source License](https://github.com/langgenius/dify/blob/main/LICENSE)：基于 Apache 2.0 并附加限制，不能简写成标准 Apache 2.0。多租户环境需书面授权；许可证将 workspace 视为 tenant。使用其前端时不能移除或修改指定品牌、版权展示。 | 参考交互和状态模型；不把其免费许可版本直接作为多租户白标 SaaS 底座。 |
| [FastGPT](https://github.com/labring/FastGPT) | 知识库、混合检索与重排、工作流、检索测试及应用评测；借鉴分段预览、检索调试和评测页面。 | [2026-09-18 提交](https://github.com/labring/FastGPT/commit/9f1d88eaa9de5aff76b14f7d3d0c71985a6f7f3f)；[v4.17.0 发布](https://github.com/labring/FastGPT/releases/tag/v4.17.0)为 09-11。 | [LICENSE](https://github.com/labring/FastGPT/blob/main/LICENSE)：Apache 2.0 加附加条款，未经书面许可不得提供类似 FastGPT 的多租户 SaaS，不能修改控制台 logo / 版权信息。[README 使用协议](https://github.com/labring/FastGPT#使用协议)对 SaaS 的表述更宽，不能自行推定包装后即可豁免。 | 参考知识库管理体验；商业托管适用范围需单独书面确认。 |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 复杂文档解析、可检查的分块、多路召回、融合重排与引用；借鉴文档到分块再到答案的可追溯关系。 | [2026-09-18 提交](https://github.com/infiniflow/ragflow/commit/302ada2cdbdd72a5db4bd8e046478e52011fe4f0)；[v0.27.2 发布](https://github.com/infiniflow/ragflow/releases/tag/v0.27.2)为 09-10。 | 根 [LICENSE](https://github.com/infiniflow/ragflow/blob/main/LICENSE)为标准 Apache 2.0，未见额外多租户或前端品牌限制；仍需遵守许可证、版权、修改声明及适用的 NOTICE 义务，不授予商标权。 | 可优先学习文档与引用设计；[官方自部署要求](https://github.com/infiniflow/ragflow#-self-hosting)至少 16 GB 内存、50 GB 磁盘，ARM64 需自行构建，不适合当前 M1 / 8 GB 整套部署。 |
| [Onyx](https://github.com/onyx-dot-app/onyx) | 企业搜索与聊天、文档连接器、Agent；借鉴外部文档权限跟随、来源连接器契约与引用界面。 | [2026-09-19 提交](https://github.com/onyx-dot-app/onyx/commit/bde5e8426f8acba938ab54b12975774ff53d1af1)。 | [根 LICENSE](https://github.com/onyx-dot-app/onyx/blob/bde5e8426f8acba938ab54b12975774ff53d1af1/LICENSE)将普通代码与 EE 目录分开，普通部分为 MIT；[backend/ee/LICENSE](https://github.com/onyx-dot-app/onyx/blob/main/backend/ee/LICENSE)要求有效企业订阅等条件才能用于生产。README 列出的 SSO、SCIM、RBAC、白标等企业能力，不能一并视为 MIT 能力。 | 标准部署含搜索、队列、模型服务等较多组件；当前只借鉴权限传播与检索证据，不 fork 全套企业搜索平台。 |
| [n8n](https://github.com/n8n-io/n8n) | 可视化自动化、连接器、AI 步骤与执行记录；借鉴重试、人工审核、步骤日志与执行历史。 | [2026-09-19 提交](https://github.com/n8n-io/n8n/commit/fd2f25a2cbacc878013cb350cf3877c6d3ed9db8)。 | [主许可证](https://github.com/n8n-io/n8n/blob/fd2f25a2cbacc878013cb350cf3877c6d3ed9db8/LICENSE.md)为 Sustainable Use License，另有 [EE 条款](https://github.com/n8n-io/n8n/blob/master/LICENSE_EE.md)。属于 source-available / fair-code，不能笼统称为宽松开源。[官方许可 FAQ](https://github.com/n8n-io/n8n-docs/blob/main/docs/privacy-and-security/sustainable-use-license.md)明确：白标收费、收费开放托管访问不在免费许可范围内；内部使用和部分后台场景可以，涉及客户自己的凭证等情形需另核实或取得商业许可。 | 可学习编排与运维体验；不以免费版直接搭建出售给多客户的工作流平台。 |
| [Langfuse](https://github.com/langfuse/langfuse) | LLM 追踪、prompt 版本、数据集、实验与评测；借鉴 run / step / trace、耗时、token、成本与反馈记录。 | [2026-09-18 提交](https://github.com/langfuse/langfuse/commit/ef0add7b2598200bd44573178d9d1cc5f7cc770b)；[v4.38.0 发布](https://github.com/langfuse/langfuse/releases/tag/v4.38.0)为 09-17。 | [根 LICENSE](https://github.com/langfuse/langfuse/blob/ef0add7b2598200bd44573178d9d1cc5f7cc770b/LICENSE)的普通部分为 MIT；`ee/`、`web/src/ee/`、`worker/src/ee/` 等为例外。[EE LICENSE](https://github.com/langfuse/langfuse/blob/main/ee/LICENSE)需合同及有效授权用于生产。不能把整仓所有功能视为 MIT。 | 最值得先借鉴的数据模型之一；先补知源自己的追踪与计量，再按需要接入工具。完整平台含额外存储与运维成本，不必复制。 |
| [Logto](https://github.com/logto-io/logto) | 身份认证、OIDC / OAuth、组织、成员与角色；借鉴组织成员关系和访问令牌设计，必要时作为外部身份服务集成。 | [2026-09-18 提交](https://github.com/logto-io/logto/commit/ec2ad0554bb593c784c2519c4e8de9aae07ef126)；[v1.43.0 发布](https://github.com/logto-io/logto/releases/tag/v1.43.0)为 08-31。 | 当前 [LICENSE](https://github.com/logto-io/logto/blob/ec2ad0554bb593c784c2519c4e8de9aae07ef126/LICENSE)为 MPL 2.0，不能沿用旧印象写 Apache。无额外 SaaS 禁止条款；文件级开源义务取决于分发等条件。网页交付的 JS 等也可能构成分发，不能将“服务器使用”泛化为全部无义务，参见 [Mozilla 官方 FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)。商标和商业服务条款另核实。 | 参考或接入认证，不重写身份平台。接入登录不等于业务数据已隔离，FastAPI / SQL / 向量检索仍必须按租户授权。 |
| [Postiz](https://github.com/gitroomhq/postiz-app) | 社交内容计划、AI 辅助、团队协作和发布；借鉴草稿、审核、排期、发布回执的数据生命周期。 | [2026-09-18 提交](https://github.com/gitroomhq/postiz-app/commit/c3e06973c8c8b58c30d153851463ec11c916bf06)；[v2.23.0 发布](https://github.com/gitroomhq/postiz-app/releases/tag/v2.23.0)为 08-04。 | [LICENSE](https://github.com/gitroomhq/postiz-app/blob/c3e06973c8c8b58c30d153851463ec11c916bf06/LICENSE)为 GNU AGPL v3，项目适用声明含 v3 或更新版本。允许商业收费，但修改版供用户网络交互时涉及向这些用户提供对应源码的义务，不能据此认定可闭源白标。README 中赞助产品的白标宣传不是 Postiz 许可授权。 | 后期再参考排期与回执；其 Next.js / NestJS / PostgreSQL / Temporal 栈会显著扩张当前项目，社交平台 OAuth、API 审核也不因 fork 而解决。 |

## 当前项目应该先借什么

1. **组织、成员与服务端租户边界。** 参考 Logto 的组织模型，身份认证与业务授权分开。先用两个租户验证：猜到文档、运行、审核、日志 ID，也不能读取或修改对方数据；检索、导出及对象存储同样受约束。请求中的租户 ID 不能替代服务端成员关系验证。
2. **运行追踪与用量账本。** 参考 Langfuse：关联请求、步骤、模型和评测版本，记录耗时、成功 / 失败、token 及可追溯的成本估算。区分业务请求和模型调用尝试，避免重试重复记账；没有供应商实际用量时明确标注未知或估算。现阶段不必接支付或复杂套餐。
3. **来源证据、状态与审核记录。** 参考 Dify / FastGPT 的检索调试与运行页面、RAGFlow 的分块引用，让面试官看到输入、候选、采用证据、失败原因、审核及修改记录。保留真实失败案例；少量人工开发样本不能证明泛化能力。

上述是实施建议，不表示知源已经完成认证、多租户、计量或生产部署。当前没有客户渠道，先让一条业务流程做到可演示、可解释、可修改、可排错，再找愿意提供资料和反馈的试点。广泛连接器、复杂计费、白标平台和全套 Obsidian 替代品均可后置。

## 核实范围与尚未核实的部分

- 已读上述 8 个根许可证，并额外检查 Onyx、Langfuse、n8n 的企业目录 / 企业许可证。Dify、FastGPT 与 RAGFlow 的许可证链接指向查询时的默认分支，可随上游更新；正式采用前应锁定版本再复核。
- 未逐一审计第三方依赖、模型权重、插件、SDK 独立包、素材及数据的许可证；根许可证不自动覆盖它们。
- 未核实厂商商业合同、白标 / 转售报价、Cloud 与自托管版本全部功能差异，也未测试隔离、安全、吞吐或升级可靠性；不把 README 中的企业能力自动归入免费版本。
- 这次未纳入 MaxKB、Casdoor，未对其当前许可证或商业适用性作结论。未以 stars 排名或替代商业判断。
- 能否采用必须结合拟使用的具体代码、版本、部署方式、改动及对外提供形式判断。这里的许可证摘要只用于筛选，不能替代适用条款原文及必要的专业审查。
