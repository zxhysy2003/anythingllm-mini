# V0 Chat Flow

V0 的目标是完成一个最小聊天闭环：HTTP 请求进入 FastAPI，经过 chat API 和
chat service，调用 DeepSeek wrapper，最后把模型回复返回给客户端。

## Main Flow

```text
.env
  -> app/core/config.py
  -> app/core/llm.py
  -> app/services/chat_service.py
  -> app/api/chat.py
  -> app/main.py
  -> POST /chat
```

1. `.env` 提供运行配置，例如 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`MODEL_NAME`。
2. `app/core/config.py` 使用 `pydantic-settings` 读取配置，并暴露 `settings`。
3. `app/core/llm.py` 定义 `DeepSeekLLM`，用 DeepSeek API 完成真正的大模型调用。
4. `app/services/chat_service.py` 定义 `ChatService`，负责聊天业务入口。
5. `app/api/chat.py` 定义 HTTP 请求模型、响应模型和 `/chat` endpoint。
6. `app/main.py` 创建 FastAPI app，并注册 chat router。

## Layer Responsibilities

### config

`app/core/config.py` 只负责配置读取，不做业务逻辑。

当前 V0 只支持 DeepSeek：

```text
LLM_PROVIDER=deepseek
MODEL_NAME=deepseek-v4-flash
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

### llm wrapper

`app/core/llm.py` 只封装 DeepSeek API 调用细节。

主要职责：

- 创建 `AsyncOpenAI` client，但 base URL 指向 DeepSeek。
- 组装 system/user messages。
- 调用 `client.chat.completions.create(...)`。
- 从 DeepSeek response 中提取文本。

V0 没有做多模型 provider factory。现在保留 `DeepSeekLLM` 这个边界，是为了以后可以学习 AnythingLLM 的 provider 包装方式，但不提前引入复杂度。

### chat service

`app/services/chat_service.py` 是最小业务层。

主要职责：

- 校验空消息。
- 校验 temperature 范围。
- 调用 `get_llm().chat(...)`。
- 返回统一的 `ChatResult`：`message`、`answer`、`provider`、`model`。
- 把底层异常包装成 `ChatServiceError`。

V0 的 chat service 本质上很薄，但它是后续扩展点：

```text
V0: message -> llm -> answer
V2: message -> rag -> llm -> answer
V3: workspace + history -> llm/rag -> save conversation
V4: message -> agent loop/tools -> final answer
```

## Chat Router

`app/api/chat.py` 使用 FastAPI 的 `APIRouter`：

```python
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResult)
async def create_chat(request: ChatRequest) -> ChatResult:
    ...
```

这里的 `router` 是一组 chat 相关 API 的集合。它还不是主应用，必须在
`app/main.py` 中注册：

```python
from app.api.chat import router as chat_router

app = FastAPI(title="AnythingLLM Mini")
app.include_router(chat_router)
```

最终路径由两部分拼起来：

```text
APIRouter(prefix="/chat") + @router.post("")
= POST /chat
```

`main.py` 里的 `/` 和 `/health` 直接使用 `@app.get(...)`，因为它们是很小的全局接口。
业务接口使用 `APIRouter`，是为了后面能按模块组织：

```text
app/api/chat.py        -> /chat
app/api/documents.py   -> /documents
app/api/workspaces.py  -> /workspaces
app/api/agents.py      -> /agents
```

这样 `main.py` 只负责创建 app 和注册 router，不会堆满业务代码。

## Request And Response

当前接口：

```http
POST /chat
```

请求体：

```json
{
  "message": "Summarize what AnythingLLM Mini can do in V0.",
  "system_prompt": "You are a helpful assistant.",
  "temperature": 0.7
}
```

响应体：

```json
{
  "message": "Summarize what AnythingLLM Mini can do in V0.",
  "answer": "...",
  "provider": "deepseek",
  "model": "deepseek-v4-flash"
}
```

`message` 会在 API 层先做 `strip()`；如果全是空白，FastAPI 会返回 422。
`temperature` 必须在 0 到 2 之间。

## Swagger

FastAPI 会自动生成 Swagger UI。

启动服务后：

```bash
uvicorn app.main:app --reload
```

访问：

```text
http://127.0.0.1:8000/docs
```

可以看到：

- `GET /`
- `GET /health`
- `POST /chat`

`ChatRequest` 里已经配置了请求示例，所以 `/docs` 中的 `/chat` 会显示 V0 示例请求。

## V0 Does Not Include

这些能力暂时不属于 V0：

- 文档上传和解析：V1
- RAG 检索：V2
- workspace 和 conversation history：V3
- agent loop 和 tools：V4
- 多模型 provider 切换：后续需要时再做

V0 结束时，只需要保证单轮聊天链路清晰、可测试、可在 Swagger 中调用。
