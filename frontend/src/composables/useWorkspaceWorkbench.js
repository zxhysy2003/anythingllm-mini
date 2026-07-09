import { computed, onMounted, ref } from "vue";

import {
  createConversation as createConversationRequest,
  createWorkspace as createWorkspaceRequest,
  listConversationMessages,
  listConversations,
  listWorkspaces,
} from "@/api/client";

function errorMessage(exc, fallback) {
  return exc instanceof Error && exc.message ? exc.message : fallback;
}

export function useWorkspaceWorkbench() {
  const workspaces = ref([]);
  const conversations = ref([]);
  const messages = ref([]);
  const selectedWorkspaceId = ref("");
  const selectedConversationId = ref("");

  const isLoadingWorkspaces = ref(false);
  const isLoadingConversations = ref(false);
  const isLoadingMessages = ref(false);
  const isCreatingWorkspace = ref(false);
  const isCreatingConversation = ref(false);

  const workspacesError = ref("");
  const conversationsError = ref("");
  const messagesError = ref("");
  const createWorkspaceError = ref("");
  const createConversationError = ref("");

  let conversationRequestId = 0;
  let messageRequestId = 0;

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

  function clearMessageState() {
    messageRequestId += 1;
    messages.value = [];
    messagesError.value = "";
    isLoadingMessages.value = false;
  }

  function clearConversationState() {
    conversationRequestId += 1;
    conversations.value = [];
    selectedConversationId.value = "";
    isLoadingConversations.value = false;
    clearMessageState();
  }

  async function loadMessages(workspaceId, conversationId) {
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
      }
    } catch (exc) {
      if (requestId === messageRequestId) {
        messages.value = [];
        messagesError.value = errorMessage(exc, "Failed to load messages.");
      }
    } finally {
      if (requestId === messageRequestId) {
        isLoadingMessages.value = false;
      }
    }
  }

  async function selectConversation(conversationId, { force = false } = {}) {
    if (!force && selectedConversationId.value === conversationId) {
      return;
    }

    selectedConversationId.value = conversationId;
    messages.value = [];
    await loadMessages(selectedWorkspaceId.value, conversationId);
  }

  async function loadConversations(workspaceId) {
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

      if (nextConversationId) {
        await selectConversation(nextConversationId, { force: true });
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

  function retryConversations() {
    return loadConversations(selectedWorkspaceId.value);
  }

  function retryMessages() {
    return loadMessages(selectedWorkspaceId.value, selectedConversationId.value);
  }

  onMounted(loadWorkspaces);

  return {
    workspaces,
    conversations,
    messages,
    selectedWorkspaceId,
    selectedConversationId,
    selectedWorkspace,
    selectedConversation,
    isLoadingWorkspaces,
    isLoadingConversations,
    isLoadingMessages,
    isCreatingWorkspace,
    isCreatingConversation,
    workspacesError,
    conversationsError,
    messagesError,
    createWorkspaceError,
    createConversationError,
    loadWorkspaces,
    retryConversations,
    retryMessages,
    selectWorkspace,
    selectConversation,
    createWorkspace,
    createConversation,
  };
}
