#!/usr/bin/env bash

set -euo pipefail

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$BACKEND_ROOT/.." && pwd)"

mkdir -p "$BACKEND_ROOT/app/api"
mkdir -p "$BACKEND_ROOT/app/core"
mkdir -p "$BACKEND_ROOT/app/models"
mkdir -p "$BACKEND_ROOT/app/services"
mkdir -p "$BACKEND_ROOT/app/tools"
mkdir -p "$BACKEND_ROOT/app/db"
mkdir -p "$PROJECT_ROOT/storage/uploads"
mkdir -p "$PROJECT_ROOT/storage/parsed"
mkdir -p "$PROJECT_ROOT/storage/chroma"
mkdir -p "$PROJECT_ROOT/storage/qdrant"
mkdir -p "$BACKEND_ROOT/tests"
mkdir -p "$PROJECT_ROOT/docs/stage-notes"

touch "$BACKEND_ROOT/app/__init__.py"
touch "$BACKEND_ROOT/app/api/__init__.py"
touch "$BACKEND_ROOT/app/core/__init__.py"
touch "$BACKEND_ROOT/app/models/__init__.py"
touch "$BACKEND_ROOT/app/services/__init__.py"
touch "$BACKEND_ROOT/app/tools/__init__.py"
touch "$BACKEND_ROOT/app/db/__init__.py"

touch "$BACKEND_ROOT/app/main.py"
touch "$BACKEND_ROOT/app/core/config.py"
touch "$BACKEND_ROOT/app/core/llm.py"
touch "$BACKEND_ROOT/app/core/embeddings.py"
touch "$BACKEND_ROOT/app/core/vectorstore.py"
touch "$BACKEND_ROOT/app/core/rag.py"
touch "$BACKEND_ROOT/app/core/agent_loop.py"

touch "$BACKEND_ROOT/app/api/workspaces.py"
touch "$BACKEND_ROOT/app/api/agents.py"

touch "$BACKEND_ROOT/app/services/chat_service.py"
touch "$BACKEND_ROOT/app/services/document_service.py"
touch "$BACKEND_ROOT/app/services/workspace_service.py"
touch "$BACKEND_ROOT/app/services/agent_service.py"

touch "$BACKEND_ROOT/app/models/user.py"
touch "$BACKEND_ROOT/app/models/workspace.py"
touch "$BACKEND_ROOT/app/models/document.py"
touch "$BACKEND_ROOT/app/models/conversation.py"

touch "$BACKEND_ROOT/app/tools/calculator.py"
touch "$BACKEND_ROOT/app/tools/document_tools.py"
touch "$BACKEND_ROOT/app/tools/registry.py"

touch "$BACKEND_ROOT/app/db/session.py"
touch "$BACKEND_ROOT/app/db/init_db.py"

touch "$PROJECT_ROOT/docs/stage-notes/01-v0-chat-flow.md"
touch "$PROJECT_ROOT/docs/stage-notes/02-document-upload-and-parse.md"
touch "$PROJECT_ROOT/docs/stage-notes/03-rag-main-flow.md"
touch "$PROJECT_ROOT/docs/stage-notes/04-workspace-and-conversation.md"
touch "$PROJECT_ROOT/docs/stage-notes/05-agent-loop-and-tools.md"
touch "$PROJECT_ROOT/docs/stage-notes/06-anythingllm-vs-mini.md"

echo "Project structure initialized."
