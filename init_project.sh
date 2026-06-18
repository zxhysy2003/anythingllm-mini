#!/usr/bin/env bash

set -e

mkdir -p app/api
mkdir -p app/core
mkdir -p app/models
mkdir -p app/services
mkdir -p app/tools
mkdir -p app/db
mkdir -p storage/uploads
mkdir -p storage/parsed
mkdir -p storage/chroma
mkdir -p storage/qdrant
mkdir -p tests
mkdir -p notes

touch app/__init__.py
touch app/api/__init__.py
touch app/core/__init__.py
touch app/models/__init__.py
touch app/services/__init__.py
touch app/tools/__init__.py
touch app/db/__init__.py

touch app/main.py
touch app/core/config.py
touch app/core/llm.py
touch app/core/embeddings.py
touch app/core/vectorstore.py
touch app/core/rag.py
touch app/core/agent_loop.py

touch app/api/chat.py
touch app/api/documents.py
touch app/api/workspaces.py
touch app/api/agents.py

touch app/services/chat_service.py
touch app/services/document_service.py
touch app/services/workspace_service.py
touch app/services/agent_service.py

touch app/models/user.py
touch app/models/workspace.py
touch app/models/document.py
touch app/models/conversation.py

touch app/tools/calculator.py
touch app/tools/document_tools.py
touch app/tools/registry.py

touch app/db/session.py
touch app/db/init_db.py

touch notes/01-v0-chat-flow.md
touch notes/02-document-upload-and-parse.md
touch notes/03-rag-main-flow.md
touch notes/04-workspace-and-conversation.md
touch notes/05-agent-loop-and-tools.md
touch notes/06-anythingllm-vs-mini.md

echo "Project structure initialized."
