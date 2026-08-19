# 面试题纲 Dify 工作流（手工导入资产）

本目录仅提供可手工导入 Dify 的 DSL 文件与契约说明。
**未导入 Dify、未访问控制台、未启动 worker、未调用 Dify、未改后端接线。**

## 文件

| 路径 | 用途 |
|---|---|
| `docs/dify/interview-question-outline-workflow.yml` | Dify Workflow DSL，应用名 **AI 招聘 - 面试题纲生成** |
| 本说明 | 输入/输出与后端契约映射、导入注意、静态校验记录 |

适用用途：仅生成结构化面试题纲。禁止招聘决策、禁止生成 Offer、禁止发送通知。

## DSL 版本与格式依据

优先使用仓库内已有可导入 YAML，而不是另写一套官方草稿。

| 项 | 值 |
|---|---|
| Dify DSL `kind` | `app` |
| Dify DSL `version` | **`0.7.0`** |
| 格式来源 | `dify/简历解析.yml`、`dify/简历多维评分.yml`（与 `dify/generate_resume_workflows.py` 同一套导出结构） |
| 图结构 | Start → LLM → Code → End（对齐简历解析，Code 节点做失败保护） |
| `dependencies` | 空数组。导入后在 LLM 节点重选本环境模型，避免把 marketplace 插件 hash 写进仓库 |

YAML 不含 API key、token、Dify app/workflow ID、真实候选人信息或真实简历/JD 正文。
LLM 节点里的 `provider` / `name` 只是与既有简历工作流一致的占位，导入后必须改成本环境可用模型。

## 与后端契约的逐字段映射

后端 live 调用当前仍被 `run_dify()` 对 `INTERVIEW_QUESTION_GENERATE` 强制 mock；下列映射对应已核实的 `build_dify_inputs()` / `InterviewQuestionGenerateResult`，供将来手工导入后接线，**本次未改该门禁**。

### 输入（Start 变量名必须完全一致）

| Dify Start 变量 | 类型 | 后端来源 | 备注 |
|---|---|---|---|
| `job_title` | string | `QuestionProviderInput.job_title` ← 岗位 `name` | `build_dify_inputs` 空值时后端会填「未命名岗位」 |
| `jd_text` | string | `QuestionProviderInput.jd_text` ← 冻结岗位版本 JD 正文 | 与简历评分的 `jd_content` 不同名，不可改 |
| `resume_text` | string | `QuestionProviderInput.resume_text` ← 已确认简历 `standardized_text` | 内存组装后才进入 Dify 映射；task JSONB snapshot 本身不含该明文 |
| `dimensions_json` | JSON 字符串 | `json.dumps(input_snapshot["dimensions"])` | 数组项为 `InterviewDimensionSnapshot`：`dimension_key` / `display_order` / `name` / `weight` / `description` / `anchors` |

LLM 节点只引用上述 4 个 Start 变量。Code 节点额外读取 `dimensions_json` 做校验，不把新字段送给模型。

### 输出（End `result`，成功时兼容 `InterviewQuestionGenerateResult`）

成功时 End 的 `result` 为对象，且**仅含** `questions`：

| 字段 | 类型 | 后端 schema | 附加校验（Code 节点，对齐 `validate_question_result_against_snapshot`） |
|---|---|---|---|
| `questions` | array，1–30 | 必填 | 缺少数组或空数组 → 结构化错误 |
| `questions[].dimension_key` | string | 非空 | 必须属于输入 `dimensions_json` 中的 key |
| `questions[].question` | string | 1–2000 | 去空白后查重（大小写不敏感） |
| `questions[].purpose` | string | 1–2000 | 非空 |
| `questions[].evidence_source` | enum | `JOB_REQUIREMENT` \| `RESUME_EXPERIENCE` \| `GENERAL` | 其它值拒绝 |
| `questions[].resume_evidence` | string \| null | 可选，≤2000；空白视为 null | `RESUME_EXPERIENCE` 必填；另两枚举必须为 null（禁止伪造） |
| `questions[].follow_up_prompts` | string[] | 最多 10，每项 1–1000 | 缺省按 `[]` |
| `questions[].risk_flags` | string[] | 最多 10，每项 1–500 | 缺省按 `[]` |
| `questions[].display_order` | integer | 1–30 | 必须从 1 连续递增到 N |

顶层或多题项出现 `hire` / `offer` 等额外字段会被拒绝。
`extra="forbid"`：成功 payload 不得携带契约外字段。

### 失败保护

JSON 无法解析，或字段/枚举/顺序/证据规则不符合约定时，Code 节点输出**结构化错误对象**，**不返回 `questions`**，更不会补造题目：

```json
{
  "error": true,
  "error_code": "output_validation_failed",
  "error_message": "json_parse_failed | schema_invalid | dimensions_json_invalid",
  "details": ["..."]
}
```

该错误对象不是合法的 `InterviewQuestionGenerateResult`，后端若将来接 live，应走校验失败路径，而不是写入题纲版本。

## 手工导入（本次未执行）

1. Dify 控制台 → 工作室 → 导入 DSL → 选择本 YAML。
2. 打开工作流，在 **LLM·题纲生成** 节点重选本环境模型。
3. 不要把 API key、workflow ID 写回本仓库文件。
4. 不要在本环境对 `INTERVIEW_QUESTION_GENERATE` 放开 `run_dify()` mock 门禁，除非另有明确授权。

## 静态校验（未启动、未执行 Dify 工作流）

用本地 Python 解析 YAML，并**抽出 Code 节点源码**在进程内调用 `main()`，不请求 Dify。

虚构示例输入（无真实姓名/电话/邮箱/公司）：

- `job_title`：示例岗位-虚构仓储接口工程师
- `jd_text`：虚构仓储系统库存查询接口与回归测试说明
- `resume_text`：虚构项目「北区演示仓」接口与回归用例
- `dimensions_json`：`D001` 接口实现、`D002` 问题排查

校验结果：

| 检查 | 结果 |
|---|---|
| YAML 可解析 | 通过（`kind=app`，`version=0.7.0`） |
| Start 变量名 | `job_title` / `jd_text` / `resume_text` / `dimensions_json` |
| LLM 变量引用 | 仅上述 4 个 Start 变量 |
| JSON schema / 输出字段 | 与 `InterviewQuestionItemResult` 8 字段一致 |
| 枚举 | `JOB_REQUIREMENT` / `RESUME_EXPERIENCE` / `GENERAL` |
| 虚构成功样例 | 3 题，三种 `evidence_source` 各一；`InterviewQuestionGenerateResult` + snapshot 规则通过 |
| 非法 JSON | `error_message=json_parse_failed`，无 `questions` |
| 非法枚举 `OTHER` | 结构化错误，无 `questions` |
| `RESUME_EXPERIENCE` 缺 `resume_evidence` | 结构化错误，无伪造题目 |
| 顶层 `hire` | 结构化错误 |
