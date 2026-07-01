# V1 Document Upload And Parse Flow

V1 的目标是完成文档上传与解析闭环：客户端上传文件，系统安全保存原文件，提取
纯文本并返回文档 metadata。

这一阶段只解决“文件如何进入系统并转换为文本”，不处理分块、Embedding、向量
存储和 RAG。

## Main Flow

```text
POST /documents/upload
  -> documents router
  -> save_upload_file()
  -> SavedDocumentFile
  -> parse_saved_file()
  -> ParsedDocumentFile
  -> DocumentUploadResult
```

主要文件：

```text
app/core/config.py                 -> 存储目录配置
app/services/document_service.py  -> 文件保存与解析
app/api/documents.py              -> 文档上传 API
app/main.py                       -> 注册 documents router
```

## Storage

默认存储目录：

```text
storage/uploads/{document_id}/{stored_filename}
storage/parsed/{document_id}/{filename_stem}.txt
```

每次上传都会生成唯一的 `document_id`。原文件和解析文本使用相同 ID，方便后续
关联文档记录、文本分块和向量数据。

## File Saving

`save_upload_file()` 负责：

- 接收 FastAPI `UploadFile`。
- 清理文件名，避免路径穿越和不安全字符。
- 只允许 `.txt`、`.pdf` 和 `.docx`。
- 使用分块读取，避免将大文件一次性加载到内存。
- 统计文件大小并保存原始文件。
- 空文件或写入失败时清理不完整文件。

保存成功后返回 `SavedDocumentFile`，记录文档 ID、文件名、类型、大小和保存路径。

## Document Parsing

`parse_saved_file()` 只处理已经安全保存的文件。解析前会验证文档 ID、文件路径、
扩展名和文件是否存在，防止读取上传目录之外的内容。

支持的解析方式：

| 格式 | 解析方式 | 当前限制 |
| --- | --- | --- |
| TXT | UTF-8 文本读取 | 不支持其他编码 |
| PDF | `pypdf` 提取文本层 | 不支持扫描版 PDF OCR |
| DOCX | `python-docx` 提取段落和表格 | 不提取图片、文本框和页眉页脚 |

DOCX 会按照原始顺序处理段落和表格。没有可解析文本、编码错误或文档损坏时，解析
会失败，但已经保存的原始文件会保留，便于排查或重新解析。

解析文本以 UTF-8 保存到 `storage/parsed`，并返回 `ParsedDocumentFile`。该模型包含
完整文本和字符数，可直接作为 V2 文本分块的输入。

## Documents API

接口：

```http
POST /documents/upload
Content-Type: multipart/form-data
```

处理顺序：

```text
UploadFile -> 保存原文件 -> 解析文本 -> 返回 metadata
```

成功时返回 HTTP `201 Created`。当前代码进入 V2 后，旧 `/documents/upload` 会继续完成
索引并返回 `chunk_count`；纯 V1 阶段只需要保存和解析 metadata。

```json
{
  "id": "8c8f...",
  "original_filename": "report.txt",
  "extension": ".txt",
  "size_bytes": 1024,
  "character_count": 980,
  "chunk_count": 3
}
```

API 不返回完整解析文本，避免大文档产生过大的响应。V3.5 后，普通 HTTP 响应也不再
暴露本地 `upload_path` 或 `parsed_path`，避免外部客户端依赖服务端文件系统路径。
这些路径仍保存在内部模型和数据库中，供删除、reconcile 和调试测试使用。

主要错误行为：

```text
不支持的格式、空文件、解析失败  -> 400
缺少上传文件                    -> 422
未预期的服务端错误              -> 500
```

## Swagger

启动应用：

```bash
conda run -n anythingllm-mini uvicorn app.main:app --reload
```

访问 `http://127.0.0.1:8000/docs`，可以直接调用 `POST /documents/upload` 上传
TXT、PDF 或 DOCX。

## Tests

V1 测试覆盖：

- 文件保存、文件名安全化和失败清理。
- TXT、PDF、DOCX 的文本提取。
- 空白内容、损坏文件和非法路径。
- HTTP 上传成功、400/422 错误和 OpenAPI multipart 定义。

运行全部测试：

```bash
conda run -n anythingllm-mini pytest -q
```

## V1 Boundary

V1 完成后的系统能力是：

```text
文件 -> 安全保存 -> 纯文本
```

以下能力留到后续阶段：

- 文件大小上限、配额和生产级存储管理。
- 数据库文档记录和 workspace 归属。
- OCR。
- 文本分块和 chunk metadata。
- Embedding、向量存储和相似度检索。

V2 将从 `ParsedDocumentFile.text` 开始，继续完成：

```text
纯文本 -> 文本分块 -> Embedding -> 向量存储 -> RAG 检索
```
