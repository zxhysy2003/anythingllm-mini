function formatErrorDetail(detail) {
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (!item || typeof item !== "object") {
          return String(item);
        }

        const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
        const message = item.msg || item.message || JSON.stringify(item);
        return location ? `${location}: ${message}` : message;
      })
      .join("; ");
  }

  if (typeof detail === "string") {
    return detail;
  }

  if (detail && typeof detail === "object") {
    return detail.message || detail.error || JSON.stringify(detail);
  }

  return "";
}

async function readPayload(response) {
  const text = await response.text();

  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      Accept: "application/json",
      ...options.headers,
    },
    ...options,
  });
  const payload = await readPayload(response);

  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? payload.detail
        : payload;
    const message = formatErrorDetail(detail);
    throw new Error(message || `Request failed with ${response.status}`);
  }

  return payload;
}

export function getHealth() {
  return request("/health");
}

export function listWorkspaces() {
  return request("/workspaces");
}

export function createWorkspace({ name }) {
  return request("/workspaces", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      name: name.trim(),
    }),
  });
}

export function listConversations(workspaceId) {
  return request(`/workspaces/${encodeURIComponent(workspaceId)}/conversations`);
}

export function createConversation(workspaceId) {
  return request(`/workspaces/${encodeURIComponent(workspaceId)}/conversations`, {
    method: "POST",
  });
}

export function listConversationMessages(workspaceId, conversationId) {
  return request(
    `/workspaces/${encodeURIComponent(workspaceId)}/conversations/${encodeURIComponent(
      conversationId,
    )}/messages`,
  );
}

export function sendChatMessage(workspaceId, conversationId, { message }) {
  return request(
    `/workspaces/${encodeURIComponent(workspaceId)}/conversations/${encodeURIComponent(
      conversationId,
    )}/chat`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: message.trim(),
      }),
    },
  );
}

export function runAgentMessage(
  workspaceId,
  conversationId,
  { message, agentMode },
) {
  return request(
    `/workspaces/${encodeURIComponent(workspaceId)}/conversations/${encodeURIComponent(
      conversationId,
    )}/agent`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: message.trim(),
        agent_mode: agentMode,
      }),
    },
  );
}
