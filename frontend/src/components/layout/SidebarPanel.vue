<script setup>
import ConversationList from "@/components/workspace/ConversationList.vue";
import WorkspaceCreateForm from "@/components/workspace/WorkspaceCreateForm.vue";
import WorkspaceList from "@/components/workspace/WorkspaceList.vue";

defineProps({
  workspaces: {
    type: Array,
    default: () => [],
  },
  conversations: {
    type: Array,
    default: () => [],
  },
  selectedWorkspaceId: {
    type: String,
    default: "",
  },
  selectedConversationId: {
    type: String,
    default: "",
  },
  workspacesLoading: {
    type: Boolean,
    default: false,
  },
  conversationsLoading: {
    type: Boolean,
    default: false,
  },
  creatingWorkspace: {
    type: Boolean,
    default: false,
  },
  creatingConversation: {
    type: Boolean,
    default: false,
  },
  workspacesError: {
    type: String,
    default: "",
  },
  conversationsError: {
    type: String,
    default: "",
  },
  createWorkspaceError: {
    type: String,
    default: "",
  },
  createConversationError: {
    type: String,
    default: "",
  },
});

defineEmits([
  "create-workspace",
  "select-workspace",
  "retry-workspaces",
  "create-conversation",
  "select-conversation",
  "retry-conversations",
]);
</script>

<template>
  <aside
    class="border-b border-border bg-muted/40 p-4 lg:max-h-[calc(100vh-3.5rem)] lg:overflow-y-auto lg:border-b-0"
  >
    <div class="space-y-6">
      <section class="space-y-3">
        <div class="flex items-center justify-between gap-3">
          <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Workspaces
          </p>
          <button
            class="text-xs font-medium text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground"
            type="button"
            @click="$emit('retry-workspaces')"
          >
            Refresh
          </button>
        </div>

        <WorkspaceCreateForm
          :error="createWorkspaceError"
          :pending="creatingWorkspace"
          @create="$emit('create-workspace', $event)"
        />

        <WorkspaceList
          :error="workspacesError"
          :items="workspaces"
          :loading="workspacesLoading"
          :selected-id="selectedWorkspaceId"
          @retry="$emit('retry-workspaces')"
          @select="$emit('select-workspace', $event)"
        />
      </section>

      <section class="space-y-3">
        <div class="flex items-center justify-between gap-3">
          <p class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Conversations
          </p>
          <button
            class="inline-flex h-8 items-center justify-center rounded-md border border-border bg-background px-2.5 text-xs font-medium transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
            :disabled="!selectedWorkspaceId || creatingConversation"
            type="button"
            @click="$emit('create-conversation')"
          >
            {{ creatingConversation ? "Creating..." : "New" }}
          </button>
        </div>

        <p v-if="createConversationError" class="text-xs leading-5 text-destructive">
          {{ createConversationError }}
        </p>

        <ConversationList
          :error="conversationsError"
          :items="conversations"
          :loading="conversationsLoading"
          :selected-id="selectedConversationId"
          @retry="$emit('retry-conversations')"
          @select="$emit('select-conversation', $event)"
        />
      </section>
    </div>
  </aside>
</template>
