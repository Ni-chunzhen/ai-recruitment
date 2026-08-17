# AI 招聘系统阶段 7 第三批：外部听记文本导入与人工校对闭环设计规格

## 1. 文档目的

本规格定义阶段 7 第三批的实施边界：面试结束后，将外部听记工具产生的文本以粘贴或 TXT/MD 文件方式导入，使用确定性规则解析为说话片段，由招聘管理员校对、保存草稿并确认不可变版本。确认版本将作为后续 AI 面试分析的唯一合法输入。

本批不录音、不处理音视频、不调用钉钉或腾讯会议听记接口、不自动识别说话人，也不执行 AI 面试评分、招聘决定或 Offer。

## 2. 已有基础与复用原则

复用前两批已经完成的：

- `InterviewRound`、`InterviewSchedule` 和状态机。
- `IN_PROGRESS → PENDING_TRANSCRIPT`。
- `recruitment.manage`、`interview.execute` 和对象级权限。
- 现有幂等、乐观锁、审计和统一错误响应。
- `DATA_ENCRYPTION_KEY`、`encrypt_secret`、`decrypt_secret` 和安全失败规则。
- 面试时间轴、候选人/Application/岗位版本关系。
- Alembic head `011_stage7_invitation_confirmation_summary`。

不得创建第二套权限、幂等、审计、加密或面试状态体系。

## 3. 本批范围

### 3.1 包含

- 粘贴纯文本。
- 上传 TXT、MD 单文件。
- UTF-8、UTF-8 BOM、GB18030 解码。
- 文件和文本安全限制。
- 确定性说话人、时间戳和段落解析。
- 导入前解析预览和人工修正。
- 不可变原始版本 `T1`。
- 可编辑草稿版本 `D1/D2...`。
- 不可变确认版本 `C1/C2...`。
- 修改说话人、纠正文案、合并、拆分、删除、排序、人工补充、听不清标记和排除分析。
- 确认版本与轮次完成的原子事务。
- 无转写完成例外及原因码。
- RBAC、对象级权限、幂等、并发、审计、加密和测试。

### 3.2 不包含

- DOCX、PDF、Excel 或其他文档格式。
- 音频、视频、录音或实时语音转文字。
- 钉钉、腾讯会议或其他听记平台接口。
- AI 说话人识别、AI 分段、AI 文本纠错。
- 导出或下载原始文件。
- 批量复制全文。
- 全文搜索索引。
- AI 单轮评分、多轮综合分析或录用建议。
- application 招聘决定或 Offer。

## 4. 核心业务语义

- `T1`：首次导入形成的不可变原始版本，每个面试轮次最多一个。
- `D1/Dn`：当前可编辑校对草稿，同一主记录同一时间最多一个。
- `C1/Cn`：人工确认的不可变版本，后续 AI 必须引用明确的确认版本 ID。
- 原始文本和历史确认版本永不被新编辑覆盖。
- 人工补充内容必须明确标识，不得伪装成原始听记。
- 删除只影响新草稿/确认快照，原始版本和旧确认版本仍保留历史内容。
- 审计记录操作统计，不记录敏感正文。

## 5. 数据模型与迁移 012

新增迁移：`012_transcript_workflow`，revises `011_stage7_invitation_confirmation_summary`。Revision ID 不超过 32 个字符，不再次修改 `alembic_version` 列宽。

### 5.1 `interview_transcripts`

每个面试轮次最多一条转写主记录。

字段：

- `id`
- `interview_round_id`，唯一外键
- `original_version_id`
- `current_draft_version_id`
- `current_confirmed_version_id`
- `version`，乐观锁
- `created_by`、`created_at`
- `updated_by`、`updated_at`

三个版本引用在表创建后补循环外键。主记录和轮次均不物理删除。

### 5.2 `interview_transcript_versions`

字段：

- `id`
- `transcript_id`
- `version_type`：`ORIGINAL / DRAFT / CONFIRMED`
- `version_no`
- `version_label`：`T1 / D1 / D2... / C1 / C2...`
- `status`：`EDITING / IMMUTABLE`
- `raw_text_encrypted`
- `source_method`：`PASTE / TXT / MD`
- `source_filename`
- `source_size`
- `source_mime`
- `source_encoding`
- `source_sha256`
- `based_on_version_id`
- `confirmed_by`、`confirmed_at`
- `created_by`、`created_at`
- `updated_by`、`updated_at`
- `version`，草稿乐观锁

约束：

- 同一主记录 `version_label` 唯一。
- 每个主记录只有一个 `ORIGINAL`。
- 每个主记录只有一个 `EDITING` 草稿，使用 PostgreSQL 部分唯一索引保证。
- `ORIGINAL` 和 `CONFIRMED` 必须为 `IMMUTABLE`。
- `DRAFT` 在编辑时必须为 `EDITING`。
- 原始文件二进制不保存。
- 原文、草稿聚合文本和确认聚合文本均加密。

### 5.3 `interview_transcript_segments`

字段：

- `id`
- `transcript_version_id`
- `segment_no`
- `speaker_key`
- `speaker_name`
- `speaker_role`：`CANDIDATE / INTERVIEWER / OTHER / UNKNOWN`
- `start_time_ms`、`end_time_ms`，可空
- `text_encrypted`
- `source_type`：`ORIGINAL / CORRECTED / MANUAL_ADDITION`
- `source_segment_refs`：来源片段 ID 数组
- `is_included_in_analysis`
- `is_unclear`
- `created_at`

约束：

- 同一版本 `segment_no` 唯一且大于 0。
- 时间戳必须同时为空或满足 `0 <= start_time_ms < end_time_ms`。
- `MANUAL_ADDITION` 必须由用户明确创建。
- 确认版本至少包含一个非空且允许参与分析的片段。
- 普通接口不得返回数据库密文。

### 5.4 索引和迁移验证

索引至少覆盖：`interview_round_id`、`transcript_id`、`version_type`、`status`、`version_label`、`transcript_version_id`、`segment_no`、`speaker_role`、`is_included_in_analysis`。

必须在隔离 PostgreSQL 测试库真实验证：

`011 → 012 → 011 → 012`

不得在开发业务库执行 downgrade。

## 6. 输入限制与安全解码

支持：

- 粘贴文本。
- `.txt`。
- `.md`。

限制：

- 单文件最大 2 MB。
- 正文最大 500,000 字符。
- 最大 10,000 个解析片段。
- 单次只允许一个文件。
- 空文件、二进制伪装、非法扩展名、超限文件和无法解码内容直接拒绝。

解码顺序：

1. UTF-8。
2. UTF-8 BOM。
3. GB18030。

文件名只保存净化后的 basename，不保存客户端路径。文件内容只在内存中处理，不写临时文件。TXT/MD 始终按纯文本处理，不执行 HTML 或 Markdown。

## 7. 两步导入

### 7.1 解析预览

`POST /api/v1/interview-rounds/{round_id}/transcripts/preview`

- 接收粘贴文本或单个文件。
- 在内存中解码、换行规范化和解析。
- 返回编码、SHA-256 摘要、字符数、片段数、命中规则和解析预览。
- 不写业务数据库。
- 不落临时文件。
- 不把原文写入日志或审计。

预览响应可包含片段正文，因为它是授权用户主动提交内容的即时返回；响应必须 `Cache-Control: no-store`。

### 7.2 确认导入

`POST /api/v1/interview-rounds/{round_id}/transcripts`

- 用户确认预览后再次提交同一文本/文件和人工修正后的片段。
- 后端重新计算原文 SHA-256 并重新解析。
- 人工调整后的片段必须引用原解析结果或明确标为 `CORRECTED`。
- 一次事务创建主记录、不可变 `T1` 和原始片段。
- 支持幂等，重复点击不得创建第二个主记录或第二个 `T1`。

后端不信任前端片段类型。凡正文、边界、说话人或角色与规则解析结果不同的片段，标记为 `CORRECTED`；前端新增且无来源片段的内容标记为 `MANUAL_ADDITION`。

## 8. 确定性解析规则

按优先级识别：

- `面试官：内容`
- `候选人：内容`
- `Speaker 1: 内容`
- `[00:01:20] 面试官：内容`
- `00:01:20 - 00:01:35 候选人：内容`

规则：

- 连续无标签行归入上一片段。
- 无上一片段时，无标签段落形成 `UNKNOWN`。
- 空行只作为段落边界。
- 不猜测 Speaker 1/2 的真实角色，默认 `UNKNOWN`，除非文本明确标注或用户人工修正。
- 时间戳统一转换为毫秒。
- 无结束时间的单点时间戳允许只作为解析显示信息；持久化时 `start/end` 同时为空，避免伪造持续时间。
- 本批不调用任何 AI 模型。

## 9. 校对草稿与版本流

### 9.1 创建草稿

- 首次校对从 `T1` 复制生成 `D1`。
- 已有确认版本后，从当前确认版本生成 `D2/D3...`。
- 重复点击“开始校对”返回当前 `EDITING` 草稿。
- 创建草稿支持幂等。

### 9.2 草稿编辑能力

- 修改说话人姓名和角色。
- 纠正正文。
- 合并相邻片段。
- 在指定位置拆分片段。
- 删除无关片段。
- 添加遗漏内容。
- 标记听不清或无法确认。
- 设置是否参与后续分析。
- 调整片段顺序。

保存时提交完整、有序的草稿片段快照，并携带 `draft_version_id`、`version` 和 `idempotency_key`。

后端必须读取旧草稿并计算差异，生成以下统计：

- 说话人修改数量。
- 文本纠正数量。
- 合并/拆分数量。
- 删除片段数量。
- 人工补充数量。
- 排除分析数量。
- 排序变化数量。

审计只记录统计、对象 ID、版本、操作者和时间，不记录正文。

来源类型规则：

- 内容和属性均未改变：`ORIGINAL`。
- 修改原始内容、边界、说话人或角色：`CORRECTED`。
- 无原始来源的新增内容：`MANUAL_ADDITION`。

删除片段不复制到当前草稿，但仍保留在 `T1` 或旧确认版本。

### 9.3 保存草稿

- 更新当前 `D1/Dn`，不生成确认版本。
- 使用乐观锁。
- 冲突返回 `409`，不得覆盖服务端数据。
- 每次保存的数据库修改、版本号和审计处于同一事务。

### 9.4 确认版本

- 当前草稿必须无非法时间、空正文和未保存修改。
- 至少存在一个非空且 `is_included_in_analysis=true` 的片段。
- 当前草稿冻结为 `IMMUTABLE`。
- 复制生成不可变 `C1/C2...`。
- 原子切换 `current_confirmed_version_id`。
- 清空 `current_draft_version_id`。
- 首次确认时，同一事务执行 `PENDING_TRANSCRIPT → COMPLETED`。
- 任何步骤失败必须全部回滚。

轮次已经 `COMPLETED` 时，招聘管理员可基于当前确认版本再次校对并形成后续 `Cn`，但不重复执行轮次状态迁移。

后续 AI 只能读取明确指定的 `CONFIRMED` 版本，不得读取 `T1` 或草稿。

## 10. 无转写完成

第一批无理由通用 `complete` 必须收紧，不能绕过第三批规则。

有转写时：只能通过确认校对完成轮次。

无转写时：只能调用独立动作：

`POST /api/v1/interview-rounds/{round_id}/complete-without-transcript`

原因码：

- `EXTERNAL_TOOL_UNAVAILABLE`
- `RECORDING_NOT_PERMITTED`
- `TRANSCRIPT_FILE_LOST`
- `CONTENT_UNUSABLE`
- `OTHER`

`OTHER` 必须填写说明。轮次记录完成模式、原因、说明、操作者和时间，但不创建伪造的转写主记录或版本。

原因码由后端接口统一提供：

`GET /api/v1/interview-transcript-reason-codes`

前端不得维护第二套硬编码回退常量。

## 11. 状态限制

- `PENDING_TRANSCRIPT` 可首次导入、校对和确认。
- `IN_PROGRESS` 不能提前导入。
- `DRAFT/SCHEDULED/CONFIRMED` 不能导入。
- `CANCELLED/ENDED_ABNORMALLY` 默认不能创建转写。
- `COMPLETED` 只能基于已有确认版本再次校对，不能重新导入第二个 `T1`。
- 每个轮次最多一个转写主记录、一个 `T1` 和一个当前草稿。
- 确认转写不改变 application 招聘决定。

## 12. 核心接口

实际路径遵循现有 API 前缀和错误格式，语义如下：

1. `POST /interview-rounds/{id}/transcripts/preview`
2. `POST /interview-rounds/{id}/transcripts`
3. `GET /interview-rounds/{id}/transcripts`
4. `GET /transcript-versions/{id}`
5. `POST /interview-transcripts/{id}/draft`
6. `PUT /transcript-versions/{draft_id}/draft`
7. `POST /transcript-versions/{draft_id}/confirm`
8. `POST /interview-rounds/{id}/complete-without-transcript`
9. `GET /interview-transcript-reason-codes`

列表只返回版本摘要。单版本详情在授权后解密片段并返回 `Cache-Control: no-store`。任何接口均不得返回数据库密文。

## 13. 权限与对象级访问

### `recruitment.manage`

- 预览和导入。
- 创建、读取和保存草稿。
- 确认版本。
- 查看原始、草稿、当前确认和历史确认版本。
- 无转写完成。

招聘管理员仍需满足项目现有 application 访问范围，不能仅凭全局权限越权读取其他业务域数据。

### `interview.execute`

被分配到该轮次时：

- 只能读取当前 `CONFIRMED` 版本。
- 不得读取 `T1`、草稿或旧确认版本。
- 不得预览、导入、编辑、确认或无转写完成。

未分配面试官返回 `404`，不暴露对象存在性。候选人无本模块入口。`audit.read` 不替代业务权限。

## 14. 前端页面与交互

### 14.1 时间轴入口

`PENDING_TRANSCRIPT`：

- 无转写：导入听记文本、无转写完成。
- 已导入：开始校对、查看原始版本。
- 有草稿：继续校对。
- 草稿满足确认条件：确认校对。

`COMPLETED`：

- 查看确认版本。
- 查看版本历史。
- 再次校对。

面试官只显示“查看已确认转写”。

### 14.2 导入抽屉

两个 Tab：粘贴文本、上传 TXT/MD。

流程：

1. 输入或选择文件。
2. 解析预览。
3. 展示编码、文件大小、SHA 摘要、字符数、片段数。
4. 预览说话人、角色、时间戳和正文。
5. 修正说话人和片段边界。
6. 二次确认后创建 `T1`。

固定提示：

> 系统仅按规则解析文本，不使用AI判断说话人。请在保存前核对敏感信息和说话人归属。

### 14.3 校对页面

路由：`/interview-rounds/:roundId/transcript`

布局遵循现有 PC 后台和“百度云控制台-实际页面”规范：

- 顶部：候选人、岗位、轮次、面试时间、版本、校对状态和保存状态。
- 左侧：版本历史 `T1/D1/C1...`。
- 中间：有序说话片段编辑区。
- 右侧：说话人、角色、时间戳、来源类型、听不清和参与分析属性。
- 底部固定栏：保存草稿、确认校对、返回时间轴。

片段操作：编辑正文、修改说话人、合并、拆分、删除、前后补充、标记听不清、排除分析、拖动排序。

视觉语义：

- `MANUAL_ADDITION` 显示醒目标记“人工补充”。
- `UNKNOWN` 和听不清片段显示警告色。
- 排除分析的片段降低视觉权重。
- 原始和确认版本只读。

确认弹窗展示当前版本、片段总数、修正/删除/补充/排除数量、未知说话人数和听不清数量，并提示确认后生成不可修改的 `Cn` 且完成本轮面试。

存在空正文、非法时间、无可分析片段或未保存修改时禁止确认。

### 14.4 并发提示

草稿乐观锁冲突显示：

> 转写草稿已被其他人员更新，请刷新后重新检查修改。

提供“刷新最新版本”，不得静默覆盖。

## 15. 安全与隐私

- 复用 `DATA_ENCRYPTION_KEY` 加密所有原始、草稿、确认聚合文本和片段正文。
- 不建立正文全文索引。
- SHA-256 用于一致性校验，不在无必要的公共响应中暴露。
- 文件内容、正文、删除片段、会议密码和完整联系方式不得进入日志、异常或审计。
- 详情响应使用 `Cache-Control: no-store`。
- 预览和导入执行文件大小、字符数、片段数和请求频率限制。
- TXT/MD 不渲染 HTML，不执行 Markdown。
- 密钥缺失、密文错误或篡改时安全失败，不返回部分内容。

## 16. 幂等、并发与事务

- 导入、创建草稿、保存草稿、确认和无转写完成复用现有幂等机制。
- 同 key、同请求返回首次结果；同 key、不同请求返回 `409`。
- 草稿保存使用乐观锁。
- 导入时锁定轮次并确认不存在 `T1`。
- 确认时锁定轮次、主记录和当前草稿。
- 确认版本、当前指针和轮次完成在一个事务中。
- 无转写完成时锁定轮次，并确认不存在转写主记录或版本。
- 重复确认不得创建第二个 `Cn` 或重复状态迁移。

## 17. 审计

必须审计：

- `interview_transcript.preview`
- `interview_transcript.import`
- `interview_transcript.draft_create`
- `interview_transcript.draft_save`
- `interview_transcript.confirm`
- `interview_transcript.view`
- `interview_transcript.complete_without_transcript`

审计字段限于：对象 ID、application ID、轮次 ID、版本 ID/标签、来源类型、字符数、片段数、变更计数、操作者、幂等/追踪字段和时间。

审计不得包含正文、删除内容、密文、密钥、完整联系方式或会议密码。

## 18. 后端测试

必须先写失败测试并确认 RED，至少覆盖：

1. 012 成为 head。
2. 011→012 迁移成功。
3. 隔离 PostgreSQL 中 `011→012→011→012` 真实通过。
4. 三张表、循环外键、唯一约束、部分唯一索引和 Check 约束存在。
5. 粘贴文本预览和导入。
6. TXT/MD 预览和导入。
7. UTF-8、UTF-8 BOM、GB18030。
8. 非法扩展名、二进制伪装、空文件和解码失败。
9. 2 MB、500,000 字符和 10,000 片段限制。
10. 预览不写业务数据库或临时文件。
11. 常见说话人和时间戳解析。
12. UNKNOWN 回退。
13. 不调用 AI。
14. 导入后端重算 SHA 和解析结果。
15. 人工修正不能伪装成 ORIGINAL。
16. 重复导入幂等且只有一个 T1。
17. T1 不可修改。
18. 原文和片段加密保存。
19. 相同明文密文不同。
20. 错误密钥和篡改安全失败。
21. D1 创建及重复创建返回现有草稿。
22. 文本纠正、说话人修改、合并、拆分、删除、人工补充、排序和排除分析。
23. `source_type` 正确继承。
24. 保存草稿的差异统计和无正文审计。
25. 草稿乐观锁冲突返回 409。
26. 确认生成不可变 C1/C2。
27. 当前确认版本原子切换。
28. 确认和轮次完成同一事务。
29. 确认失败全部回滚。
30. 已完成轮次再次校对不重复迁移状态。
31. 无转写原因码。
32. OTHER 缺说明被拒绝。
33. 通用 complete 不能绕过转写规则。
34. 有转写时不能走无转写完成。
35. 招聘管理员完整权限和业务域范围。
36. 面试官只能读取被分配轮次的当前确认版本。
37. 面试官不能读取 T1、草稿或旧 Cn。
38. 未分配面试官返回 404。
39. 列表不返回正文。
40. 详情不返回密文且 `Cache-Control: no-store`。
41. 审计和日志不包含正文。
42. 本批不创建 AI 任务，不改变 application 决定。

测试必须验证 HTTP 行为和数据库最终状态，不使用源码字符串计数代替行为测试。

## 19. 前端测试

至少覆盖：

1. 时间轴各状态入口。
2. 粘贴与 TXT/MD Tab。
3. DOCX和音视频不可选。
4. 解析预览元数据。
5. UNKNOWN 和非 AI 提示。
6. 预览片段修正。
7. 导入二次确认。
8. T1、D1、C1/C2 版本历史。
9. 原始和确认版本只读。
10. 说话人和角色修改。
11. 文本纠正。
12. 合并、拆分、删除、添加和排序。
13. 人工补充标签。
14. 听不清和排除分析。
15. 保存草稿携带 version 和非空 idempotency_key。
16. 未保存修改提示。
17. 确认统计。
18. 空正文、非法时间、无分析片段和未保存修改阻断确认。
19. 确认后刷新时间轴为 COMPLETED。
20. 再次校对形成后续版本。
21. 无转写完成原因码从 API 加载。
22. OTHER 说明校验。
23. 409 刷新提示。
24. 面试官只读当前确认版本。
25. 无权限不显示写操作。
26. 不显示 DOCX、音视频、自动转写、AI评分或 Offer。

Mock API 边界，不复制组件内部实现逻辑。

## 20. 验证命令

完成后运行：

- 012 迁移单元测试。
- 012 PostgreSQL live 迁移与约束测试。
- 文本解码、解析、限制和预览测试。
- 转写加密、版本、草稿、确认和状态 Service 测试。
- 转写 API、RBAC、幂等、并发和审计测试。
- 第一、第二批面试相关回归测试。
- 转写页面及时间轴组件测试。
- 前端全量 Vitest。
- `pnpm type-check`。
- `pnpm build`。
- 后端相关 pytest。
- 后端全量 pytest。
- 隔离测试库 `011→012→011→012`。
- 开发库仅执行 `alembic upgrade head`。
- `alembic current`，目标为 `012_transcript_workflow`。

## 21. 实施限制

- 不修改 008、009、010 或 011 历史迁移。
- 不在开发业务库执行 downgrade。
- 不使用超过 32 字符的 revision ID。
- 不处理 DOCX、音频、视频或录音。
- 不调用外部听记接口。
- 不调用 AI 自动解析、纠错、评分或决策。
- 不建立全文搜索。
- 不导出或下载原始文件。
- 不创建 notification task。
- 不改变 application 招聘决定。
- 不做 Offer。
- 不修改 Dify YML。
- 不进行无关重构。

## 22. 完成报告

完成后停止并报告：

1. 实际复用的状态、权限、加密、幂等和审计实现。
2. 012 迁移、三张表、索引和约束。
3. 文件类型、解码和限制实现。
4. 确定性解析规则及测试。
5. T1/Dn/Cn 版本和加密证据。
6. 校对操作及来源类型证据。
7. 草稿并发和 409 证据。
8. 确认版本与轮次完成的事务证据。
9. 无转写完成和通用 complete 收紧证据。
10. RBAC 与越权访问证据。
11. 审计和日志脱敏证据。
12. 前端导入、预览、校对、确认和版本历史证据。
13. 本批未进入外部转写、AI、招聘决定或 Offer 的证据。
14. RED 测试证据。
15. GREEN、type-check 和 build 结果。
16. PostgreSQL live 迁移结果。
17. 当前 Alembic 版本。

## 23. 验收标准

本批验收通过必须同时满足：

- PENDING_TRANSCRIPT 轮次能通过粘贴或 TXT/MD 创建唯一不可变 T1。
- 预览和解析不调用 AI，不落临时文件。
- 招聘管理员可完成完整人工校对并生成不可变 Cn。
- 原始、草稿、确认和片段正文均加密保存。
- 人工补充、纠正、删除和排除分析的语义可追溯。
- 确认版本和轮次完成处于一个事务。
- 无转写完成具有明确原因，通用 complete 不能绕过规则。
- 面试官只能读取被分配轮次的当前确认版本。
- 幂等、乐观锁、对象级权限和审计脱敏有效。
- 全量测试、类型检查、构建和 PostgreSQL live 迁移通过。
- 未越界进入 DOCX、音视频、外部听记接口、AI分析、招聘决定或 Offer。
