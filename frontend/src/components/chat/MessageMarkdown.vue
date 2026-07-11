<script setup>
import { onBeforeUnmount, ref, watch } from "vue";

import { renderMarkdown } from "@/lib/markdownRenderer";

const props = defineProps({
  content: {
    type: String,
    default: "",
  },
});

const renderedHtml = ref("");
const codeBlocks = ref([]);
const copyFeedback = ref("");

let renderRequestId = 0;
let feedbackTimer;

async function renderContent() {
  const requestId = ++renderRequestId;
  const result = await renderMarkdown(props.content);

  if (requestId !== renderRequestId) {
    return;
  }

  renderedHtml.value = result.html;
  codeBlocks.value = result.codeBlocks;
}

function setCopyFeedback(button, message, restoreLabel) {
  window.clearTimeout(feedbackTimer);
  button.textContent = message;
  copyFeedback.value = message;

  feedbackTimer = window.setTimeout(() => {
    if (button.isConnected) {
      button.textContent = restoreLabel;
    }
    copyFeedback.value = "";
  }, 1800);
}

async function handleRenderedClick(event) {
  const button = event.target?.closest?.("[data-code-index]");
  if (!button) {
    return;
  }

  event.stopPropagation();
  const codeIndex = Number(button.dataset.codeIndex);
  const code = codeBlocks.value[codeIndex];

  if (!Number.isInteger(codeIndex) || typeof code !== "string") {
    setCopyFeedback(button, "Copy failed", "Copy");
    return;
  }

  try {
    if (!navigator.clipboard?.writeText) {
      throw new Error("Clipboard access is unavailable.");
    }

    await navigator.clipboard.writeText(code);
    setCopyFeedback(button, "Copied", "Copy");
  } catch {
    setCopyFeedback(button, "Copy failed", "Copy");
  }
}

watch(() => props.content, renderContent, { immediate: true });

onBeforeUnmount(() => {
  window.clearTimeout(feedbackTimer);
});
</script>

<template>
  <div class="message-markdown" @click="handleRenderedClick" v-html="renderedHtml" />
  <p v-if="copyFeedback" class="sr-only" role="status">{{ copyFeedback }}</p>
</template>
