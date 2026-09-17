import { computed, onMounted, ref } from "vue";

import {
  createConversation as createConversationRequest,
  createWorkspace as createWorkspaceRequest,
  listConversationMessages,
  listConversations,
  listWorkspaces,
  runAgentMessage as runAgentMessageRequest,
  sendChatMessage as sendChatMessageRequest,
} from "@/api/client";

const RUN_TYPE_AGENT = "agent";
const RUN_TYPE_CHAT = "chat";
const DEFAULT_AGENT_MODE = "react_text";

function errorMessage(exc, fallback) {
  return exc instanceof Error && exc.message ? exc.message : fallback;
}

export function useWorkspaceWorkbench() {
  const workspaces = ref([]);
  const conversations = ref([]);
  const messages = ref([]);
  const selectedWorkspaceId = ref("");
  const selectedConversationId = ref("");
  const selectedAssistantMessageId = ref("");

  const isLoadingWorkspaces = ref(false);
  const isLoadingConversations = ref(false);
  const isLoadingMessages = ref(false);
  const isCreatingWorkspace = ref(false);
  const isCreatingConversation = ref(false);
  const isSendingMessage = ref(false);

  const workspacesError = ref("");
  const conversationsError = ref("");
  const messagesError = ref("");
  const createWorkspaceError = ref("");
  const createConversationError = ref("");
  const sendMessageError = ref("");
  const lastRunType = ref("");
  const lastRunMetrics = ref(null);
  const sendMessageSuccessCount = ref(0);

  let conversationRequestId = 0;
  let messageRequestId = 0;
  let sendMessageRequestId = 0;

  const selectedWorkspace = computed(
    () =>
      workspaces.value.find((workspace) => workspace.id === selectedWorkspaceId.value) ||
      null,
  );
  const selectedConversation = computed(
    () =>
      conversations.value.find(
        (conversation) => conversation.id === selectedConversationId.value,
      ) || null,
  );
  const selectedAssistantMessage = computed(
    () =>
      messages.value.find(
        (message) =>
          message.id === selectedAssistantMessageId.value && message.role === "assistant",
      ) || null,
  );

  function selectLatestAssistantMessage(items) {
    for (let index = items.length - 1; index >= 0; index -= 1) {
      if (items[index].role === "assistant") {
        selectedAssistantMessageId.value = items[index].id;
        return;
      }
    }

    selectedAssistantMessageId.value = "";
  }

  function reconcileSelectedAssistantMessage(items, { selectLatest = false } = {}) {
    const selectedStillExists = items.some(
      (message) =>
        message.id === selectedAssistantMessageId.value && message.role === "assistant",
    );

    if (selectLatest || !selectedStillExists) {
      selectLatestAssistantMessage(items);
    }
  }

  function clearCurrentRunState() {
    sendMessageRequestId += 1;
    isSendingMessage.value = false;
    sendMessageError.value = "";
    lastRunType.value = "";
    lastRunMetrics.value = null;
  }

  function clearMessageState() {
    messageRequestId += 1;
    messages.value = [];
    selectedAssistantMessageId.value = "";
    messagesError.value = "";
    isLoadingMessages.value = false;
    clearCurrentRunState();
  }

  function clearConversationState() {
    conversationRequestId += 1;
    conversations.value = [];
    selectedConversationId.value = "";
    isLoadingConversations.value = false;
    clearMessageState();
  }

  async function loadMessages(
    workspaceId,
    conversationId,
    { selectLatestAssistant = false } = {},
  ) {
    if (!workspaceId || !conversationId) {
      clearMessageState();
      return;
    }

    const requestId = ++messageRequestId;
    isLoadingMessages.value = true;
    messagesError.value = "";

    try {
      const payload = await listConversationMessages(workspaceId, conversationId);
      if (requestId === messageRequestId) {
        messages.value = payload;
        reconcileSelectedAssistantMessage(payload, {
          selectLatest: selectLatestAssistant,
        });
      }
    } catch (exc) {
      if (requestId === messageRequestId) {
        messages.value = [];
        selectedAssistantMessageId.value = "";
        messagesError.value = errorMessage(exc, "Failed to load messages.");
      }
    } finally {
      if (requestId === messageRequestId) {
        isLoadingMessages.value = false;
      }
    }
  }

  async function selectConversation(conversationId, { force = false } = {}) {
    const selectionChanged = selectedConversationId.value !== conversationId;

    if (!force && !selectionChanged) {
      return;
    }

    selectedConversationId.value = conversationId;
    messages.value = [];
    messagesError.value = "";
    if (selectionChanged) {
      clearCurrentRunState();
    }
    await loadMessages(selectedWorkspaceId.value, conversationId, {
      selectLatestAssistant: true,
    });
  }

  async function loadConversations(workspaceId, { reloadMessages = true } = {}) {
    if (!workspaceId) {
      clearConversationState();
      return;
    }

    const requestId = ++conversationRequestId;
    isLoadingConversations.value = true;
    conversationsError.value = "";
    createConversationError.value = "";

    try {
      const payload = await listConversations(workspaceId);
      if (requestId !== conversationRequestId) {
        return;
      }

      conversations.value = payload;
      const selectedStillExists = payload.some(
        (conversation) => conversation.id === selectedConversationId.value,
      );
      const nextConversationId = selectedStillExists
        ? selectedConversationId.value
        : payload[0]?.id || "";

      if (nextConversationId && reloadMessages) {
        await selectConversation(nextConversationId, { force: true });
      } else if (nextConversationId) {
        selectedConversationId.value = nextConversationId;
      } else {
        selectedConversationId.value = "";
        clearMessageState();
      }
    } catch (exc) {
      if (requestId === conversationRequestId) {
        clearConversationState();
        conversationsError.value = errorMessage(exc, "Failed to load conversations.");
      }
    } finally {
      if (requestId === conversationRequestId) {
        isLoadingConversations.value = false;
      }
    }
  }

  async function refreshConversationList(workspaceId) {
    if (!workspaceId) {
      return;
    }

    const requestId = ++conversationRequestId;
    isLoadingConversations.value = true;
    conversationsError.value = "";

    try {
      const payload = await listConversations(workspaceId);
      if (
        requestId === conversationRequestId &&
        selectedWorkspaceId.value === workspaceId
      ) {
        conversations.value = payload;
      }
    } catch (exc) {
      if (
        requestId === conversationRequestId &&
        selectedWorkspaceId.value === workspaceId
      ) {
        conversationsError.value = errorMessage(
          exc,
          "Failed to load conversations.",
        );
      }
    } finally {
      if (requestId === conversationRequestId) {
        isLoadingConversations.value = false;
      }
    }
  }

  async function selectWorkspace(workspaceId, { force = false } = {}) {
    if (!force && selectedWorkspaceId.value === workspaceId) {
      return;
    }

    selectedWorkspaceId.value = workspaceId;
    clearConversationState();
    await loadConversations(workspaceId);
  }

  async function loadWorkspaces() {
    isLoadingWorkspaces.value = true;
    workspacesError.value = "";
    createWorkspaceError.value = "";

    try {
      const payload = await listWorkspaces();
      workspaces.value = payload;

      const selectedStillExists = payload.some(
        (workspace) => workspace.id === selectedWorkspaceId.value,
      );
      const nextWorkspaceId = selectedStillExists
        ? selectedWorkspaceId.value
        : payload[0]?.id || "";

      if (nextWorkspaceId) {
        await selectWorkspace(nextWorkspaceId, { force: true });
      } else {
        selectedWorkspaceId.value = "";
        clearConversationState();
      }
    } catch (exc) {
      workspaces.value = [];
      selectedWorkspaceId.value = "";
      clearConversationState();
      workspacesError.value = errorMessage(exc, "Failed to load workspaces.");
    } finally {
      isLoadingWorkspaces.value = false;
    }
  }

  async function createWorkspace(name) {
    const trimmedName = name.trim();
    createWorkspaceError.value = "";

    if (!trimmedName) {
      createWorkspaceError.value = "Workspace name is required.";
      return false;
    }

    isCreatingWorkspace.value = true;

    try {
      const workspace = await createWorkspaceRequest({ name: trimmedName });
      workspaces.value = [
        workspace,
        ...workspaces.value.filter((item) => item.id !== workspace.id),
      ];
      await selectWorkspace(workspace.id, { force: true });
      return true;
    } catch (exc) {
      createWorkspaceError.value = errorMessage(exc, "Failed to create workspace.");
      return false;
    } finally {
      isCreatingWorkspace.value = false;
    }
  }

  async function createConversation() {
    createConversationError.value = "";
    const workspaceId = selectedWorkspaceId.value;

    if (!workspaceId) {
      createConversationError.value = "Select a workspace first.";
      return false;
    }

    isCreatingConversation.value = true;

    try {
      const conversation = await createConversationRequest(workspaceId);
      if (selectedWorkspaceId.value !== workspaceId) {
        return false;
      }

      conversations.value = [
        conversation,
        ...conversations.value.filter((item) => item.id !== conversation.id),
      ];
      await selectConversation(conversation.id, { force: true });
      return true;
    } catch (exc) {
      if (selectedWorkspaceId.value === workspaceId) {
        createConversationError.value = errorMessage(
          exc,
          "Failed to create conversation.",
        );
      }
      return false;
    } finally {
      isCreatingConversation.value = false;
    }
  }

  async function sendMessage({
    message,
    runType = RUN_TYPE_CHAT,
    agentMode = DEFAULT_AGENT_MODE,
  } = {}) {
    const trimmedMessage = typeof message === "string" ? message.trim() : "";
    const workspaceId = selectedWorkspaceId.value;
    const conversationId = selectedConversationId.value;

    sendMessageError.value = "";

    if (isSendingMessage.value) {
      return false;
    }

    if (!workspaceId || !conversationId) {
      sendMessageError.value = "Select a conversation first.";
      return false;
    }

    if (!trimmedMessage) {
      sendMessageError.value = "Message is required.";
      return false;
    }

    const normalizedRunType = runType === RUN_TYPE_AGENT ? RUN_TYPE_AGENT : RUN_TYPE_CHAT;
    const requestId = ++sendMessageRequestId;
    isSendingMessage.value = true;
    lastRunType.value = normalizedRunType;
    lastRunMetrics.value = null;

    try {
      const result =
        normalizedRunType === RUN_TYPE_AGENT
          ? await runAgentMessageRequest(workspaceId, conversationId, {
              message: trimmedMessage,
              agentMode,
            })
          : await sendChatMessageRequest(workspaceId, conversationId, {
              message: trimmedMessage,
            });

      if (
        requestId !== sendMessageRequestId ||
        selectedWorkspaceId.value !== workspaceId ||
        selectedConversationId.value !== conversationId
      ) {
        return false;
      }

      lastRunMetrics.value = {
        ...(result?.metrics || {}),
        ...(normalizedRunType === RUN_TYPE_AGENT
          ? {
              agent_invocation_id: result?.agent_invocation_id,
              agent_mode: agentMode,
            }
          : {}),
      };

      await loadMessages(workspaceId, conversationId, {
        selectLatestAssistant: true,
      });

      if (
        requestId !== sendMessageRequestId ||
        selectedWorkspaceId.value !== workspaceId ||
        selectedConversationId.value !== conversationId
      ) {
        return false;
      }

      await refreshConversationList(workspaceId);

      if (
        requestId === sendMessageRequestId &&
        selectedWorkspaceId.value === workspaceId &&
        selectedConversationId.value === conversationId
      ) {
        sendMessageSuccessCount.value += 1;
        return true;
      }

      return false;
    } catch (exc) {
      if (
        requestId === sendMessageRequestId &&
        selectedWorkspaceId.value === workspaceId &&
        selectedConversationId.value === conversationId
      ) {
        sendMessageError.value = errorMessage(exc, "Failed to send message.");
      }
      return false;
    } finally {
      if (requestId === sendMessageRequestId) {
        isSendingMessage.value = false;
      }
    }
  }

  function retryConversations() {
    return loadConversations(selectedWorkspaceId.value);
  }

  function retryMessages() {
    return loadMessages(selectedWorkspaceId.value, selectedConversationId.value);
  }

  function selectAssistantMessage(messageId) {
    const message = messages.value.find(
      (item) => item.id === messageId && item.role === "assistant",
    );

    if (message) {
      selectedAssistantMessageId.value = message.id;
    }
  }

  onMounted(loadWorkspaces);

  return {
    workspaces,
    conversations,
    messages,
    selectedWorkspaceId,
    selectedConversationId,
    selectedAssistantMessageId,
    selectedWorkspace,
    selectedConversation,
    selectedAssistantMessage,
    isLoadingWorkspaces,
    isLoadingConversations,
    isLoadingMessages,
    isCreatingWorkspace,
    isCreatingConversation,
    isSendingMessage,
    workspacesError,
    conversationsError,
    messagesError,
    createWorkspaceError,
    createConversationError,
    sendMessageError,
    lastRunType,
    lastRunMetrics,
    sendMessageSuccessCount,
    loadWorkspaces,
    retryConversations,
    retryMessages,
    selectWorkspace,
    selectConversation,
    selectAssistantMessage,
    createWorkspace,
    createConversation,
    sendMessage,
  };
}
