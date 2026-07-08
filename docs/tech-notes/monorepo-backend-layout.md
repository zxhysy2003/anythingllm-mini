# 技术名称：Backend 目录迁移

## 1. 这是什么？

`anythingllm-mini` 现在采用轻量 monorepo 目录边界：

```text
anythingllm-mini/
  backend/          # FastAPI 后端代码、测试、Alembic 和后端环境文件
  docs/             # 项目本地文档
  storage/          # 本地上传、解析文件和向量库数据
  .env              # 本地运行配置
  .env.example      # 配置示例
  anythingllm_mini.db
```

这次迁移只改变目录结构，不改变 HTTP API、数据库 schema、Agent loop 行为或运行数据位置。

## 2. 目录职责

`backend/` 放后端工程内容：

- `app/`：FastAPI、service、model、tool、RAG、agent loop。
- `tests/`：后端测试。
- `alembic/` 和 `alembic.ini`：数据库迁移。
- `pytest.ini`、`environment.yml`、`init_project.sh`：后端开发配置。

仓库根目录继续放跨端和运行态内容：

- `.env` 和 `.env.example`：本地配置入口。
- `storage/`：上传文件、解析文本、Chroma/Qdrant 本地数据。
- `anythingllm_mini.db`：默认 SQLite 数据库。
- `docs/`：学习文档和技术说明。

当前 `backend/app/web/agent_ui.html` 仍是后端提供的本地调试页，用来替代重复 `curl`。正式前端如果以后加入，应作为单独 `frontend/` 能力线处理。

## 3. 路径约定

`backend/app/core/config.py` 定义两个根目录：

- `PROJECT_ROOT`：指向 `anythingllm-mini/`。
- `BACKEND_ROOT`：指向 `anythingllm-mini/backend/`。

运行数据按 `PROJECT_ROOT` 解析：

- `.env`
- `anythingllm_mini.db`
- `storage/chroma`
- `storage/uploads`
- `storage/parsed`

后端工具按 `BACKEND_ROOT` 解析：

- `backend/alembic.ini`
- `backend/alembic/`

这样从 `backend/` 运行命令时，不会生成第二份 `backend/anythingllm_mini.db` 或 `backend/storage/`。

## 4. 常用命令

后端命令默认从 `backend/` 运行：

```bash
cd backend
conda run -n anythingllm-mini python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

测试和格式检查：

```bash
cd backend
conda run -n anythingllm-mini pytest -q
conda run -n anythingllm-mini ruff check app tests alembic
conda run -n anythingllm-mini black --check app tests alembic
git diff --check
```

Alembic：

```bash
cd backend
conda run -n anythingllm-mini alembic upgrade head
conda run -n anythingllm-mini alembic revision --autogenerate -m "add xxx"
```

## 5. 学习价值

这次迁移的重点不是新增功能，而是明确工程边界：

- 后端代码可以独立运行和测试。
- 未来 `frontend/` 不会和 Python 包、测试、Alembic 配置混在根目录。
- 运行数据保持稳定，避免因为工作目录变化造成调试困惑。
- `PROJECT_ROOT` 和 `BACKEND_ROOT` 把“项目边界”和“后端边界”分开表达。

历史 `docs/learning-log/` 中可能仍引用迁移前的 `app/`、`tests/` 路径；这些内容保留为当时的学习记录。阅读当前代码时，以 `backend/` 下的路径为准。
