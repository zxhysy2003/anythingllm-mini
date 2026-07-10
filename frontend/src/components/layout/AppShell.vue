<script setup>
import MessageList from "@/components/chat/MessageList.vue";
import PromptComposer from "@/components/chat/PromptComposer.vue";
import HealthBadge from "@/components/ui/HealthBadge.vue";
import { useBackendHealth } from "@/composables/useBackendHealth";
import { useWorkspaceWorkbench } from "@/composables/useWorkspaceWorkbench";

import InspectorPanel from "./InspectorPanel.vue";
import SidebarPanel from "./SidebarPanel.vue";

const { status, error, refresh } = useBackendHealth();
const {
  workspaces,
  conversations,
  messages,
  selectedWorkspaceId,
  selectedConversationId,
  selectedWorkspace,
  selectedConversation,
  lastAssistantMessage,
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
  createWorkspace,
  createConversation,
  sendMessage,
} = useWorkspaceWorkbench();
</script>

<template>
  <div class="min-h-screen bg-background text-foreground">
    <header class="flex h-14 items-center justify-between border-b border-border px-4">
      <div>
        <p class="text-sm font-semibold leading-none">AnythingLLM Mini</p>
        <p class="mt-1 max-w-[56vw] truncate text-xs text-muted-foreground">
          {{ selectedWorkspace ? selectedWorkspace.name : "Workspace workbench" }}
        </p>
      </div>

      <HealthBadge :status="status" :error="error" @refresh="refresh" />
    </header>

    <main
      class="grid min-h-[calc(100vh-3.5rem)] grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)_320px]"
    >
      <SidebarPanel
        :conversations="conversations"
        :conversations-error="conversationsError"
        :conversations-loading="isLoadingConversations"
        :create-conversation-error="createConversationError"
        :create-workspace-error="createWorkspaceError"
        :creating-conversation="isCreatingConversation"
        :creating-workspace="isCreatingWorkspace"
        :selected-conversation-id="selectedConversationId"
        :selected-workspace-id="selectedWorkspaceId"
        :workspaces="workspaces"
        :workspaces-error="workspacesError"
        :workspaces-loading="isLoadingWorkspaces"
        @create-conversation="createConversation"
        @create-workspace="createWorkspace"
        @retry-conversations="retryConversations"
        @retry-workspaces="loadWorkspaces"
        @select-conversation="selectConversation"
        @select-workspace="selectWorkspace"
      />

      <section class="flex min-h-[calc(100vh-3.5rem)] flex-col border-b border-border lg:border-x lg:border-b-0">
        <div class="border-b border-border px-5 py-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="truncate text-sm font-medium">
                {{ selectedConversation ? selectedConversation.title : "Conversation" }}
              </p>
              <p class="mt-1 truncate text-sm text-muted-foreground">
                {{
                  selectedWorkspace
                    ? selectedWorkspace.name
                    : "Create a workspace to begin."
                }}
              </p>
            </div>
            <div class="text-right text-xs text-muted-foreground">
              <p>{{ workspaces.length }} workspace{{ workspaces.length === 1 ? "" : "s" }}</p>
              <p>
                {{ conversations.length }} conversation{{
                  conversations.length === 1 ? "" : "s"
                }}
              </p>
            </div>
          </div>
        </div>

        <MessageList
          :error="messagesError"
          :has-conversation="Boolean(selectedConversation)"
          :items="messages"
          :loading="isLoadingMessages"
          @retry="retryMessages"
        />

        <PromptComposer
          :disabled="!selectedConversation || isLoadingConversations || isLoadingMessages"
          :error="sendMessageError"
          :has-conversation="Boolean(selectedConversation)"
          :pending="isSendingMessage"
          :success-count="sendMessageSuccessCount"
          @submit="sendMessage"
        />
      </section>

      <InspectorPanel
        :conversation-count="conversations.length"
        :last-assistant-message="lastAssistantMessage"
        :last-run-metrics="lastRunMetrics"
        :last-run-type="lastRunType"
        :message-count="messages.length"
        :selected-conversation="selectedConversation"
        :selected-workspace="selectedWorkspace"
        :workspace-count="workspaces.length"
      />
    </main>
  </div>
</template>
