# 文档文件名信任边界重构

## 目标

文件名不再同时承担展示、身份、磁盘定位和 LLM 元数据四种职责。本次重构采用四个明确边界：

| 边界 | 生命周期 | 允许传播 | 禁止传播 |
| --- | --- | --- | --- |
| `raw_filename` | 仅上传请求期间 | filename policy | 数据库、路径、日志、LLM、tool artifacts |
| `display_filename` | 文档生命周期 | API、文档列表、citation、UI | 磁盘路径、Map/Reduce prompt、RAG reference prompt |
| `document_id` | 文档生命周期 | 数据库关联、工具 selector、向量关联、日志 | 不作为用户可读名称 |
| `storage_path` | 服务内部运行时 | 文件读取、解析、清理、删除 | API、tool artifacts、source、replay、LLM |

`display_filename` 只保证字符串适合协议和展示，不代表其中的自然语言可信。类似
`ignore previous instructions.pdf` 的名称仍是合法标签；Agent 必须把标签和文档正文都视为不可信数据。

## 统一测试矩阵

| 输入或场景 | 预期 | 允许传播 | 禁止传播 |
| --- | --- | --- | --- |
| `guide.txt`、中文、emoji、多点名称 | 保留可读名称 | API/list/source/UI | storage path、summary/RAG prompt |
| `.PDF`、`.DOCX` | 展示保持原大小写，内部扩展名转小写 | display 与 extension | 原始扩展名参与路径 |
| Unix、Windows、`C:\fakepath` | 只取最后一个 basename | 规范化后的 display | 原始路径前缀 |
| Unicode NFD | 规范化为 NFC | NFC display | 非规范化变体 |
| 空值、`.`、`..`、点开头 | 上传失败 | 错误码 | 数据库和文件系统 |
| 控制字符、格式字符、`Zl`、`Zp` | 上传失败 | 错误码 | 日志、LLM、持久化 |
| 超过 255 字符 | 上传失败 | 错误码 | 下游 |
| 无扩展名或非 TXT/PDF/DOCX | 上传失败 | 错误码 | 文件系统 |
| 语义 prompt-injection 名称 | 允许作为 display | list/source/UI | Map/Reduce、RAG reference prompt |
| 两个相同 display 名称 | 都可注册 | list | filename selector |
| summarize selector | 只接受 32 位小写十六进制 ID | tool input | filename |
| 新上传路径 | `source.<ext>`、`content.txt` | 服务内部 | API/source/replay |
| 路径越界或外部 symlink | 读取和删除前失败 | 安全错误 | 外部文件访问 |
| 旧数据库含文档 | migration 在 DDL 前失败 | 重置说明 | 半迁移 schema |
| 空旧数据库 | migration 成功 | final schema | 旧 filename/path 列 |
| UI 渲染 display | 使用 `textContent` | 文本节点 | `innerHTML` |

## 破坏升级边界

- `WorkspaceDocument` 只保留 `display_filename`，不保留 raw/stored filename 或本地路径。
- summary 工具只接受 `document_id`；相同展示名不会造成 selector 歧义。
- Chroma metadata 和 source contract 使用 `display_filename`。
- replay schema 升级到 version 2，不读取 version 1 fixture。
- 旧数据库、storage 和 Chroma 数据不自动删除或迁移。存在文档记录时 migration 明确失败，
  用户需要人工清理后重新上传。

默认本地配置的破坏升级步骤是：先备份仍需保留的内容，停止应用，然后在项目根目录人工删除
`anythingllm_mini.db` 和 `storage/`，最后重新启动并上传文档。若 `.env` 覆盖了数据库或 storage
位置，应清理对应的显式路径，不能照搬默认路径。应用和 migration 本身不会执行删除。
