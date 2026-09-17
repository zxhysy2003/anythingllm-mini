import MarkdownIt from "markdown-it";
import { createBundledHighlighter } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

const HIGHLIGHT_THEME = "github-light";
const SUPPORTED_LANGUAGES = [
  "bash",
  "css",
  "html",
  "javascript",
  "json",
  "markdown",
  "plaintext",
  "python",
  "sql",
  "typescript",
];
const LANGUAGE_LOADERS = {
  bash: () => import("@shikijs/langs/bash"),
  css: () => import("@shikijs/langs/css"),
  html: () => import("@shikijs/langs/html"),
  javascript: () => import("@shikijs/langs/javascript"),
  json: () => import("@shikijs/langs/json"),
  markdown: () => import("@shikijs/langs/markdown"),
  python: () => import("@shikijs/langs/python"),
  sql: () => import("@shikijs/langs/sql"),
  typescript: () => import("@shikijs/langs/typescript"),
};
const LANGUAGE_ALIASES = {
  js: "javascript",
  jsx: "javascript",
  md: "markdown",
  py: "python",
  sh: "bash",
  shell: "bash",
  text: "plaintext",
  ts: "typescript",
  tsx: "typescript",
  txt: "plaintext",
  yaml: "plaintext",
  yml: "plaintext",
};

let highlighterPromise;

const createHighlighter = createBundledHighlighter({
  engine: () => createJavaScriptRegexEngine(),
  langs: LANGUAGE_LOADERS,
  themes: {
    [HIGHLIGHT_THEME]: () => import("@shikijs/themes/github-light"),
  },
});

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function normalizeLanguage(info) {
  const rawLanguage = String(info || "")
    .trim()
    .split(/\s+/, 1)[0]
    .toLowerCase();
  const language = LANGUAGE_ALIASES[rawLanguage] || rawLanguage;

  return SUPPORTED_LANGUAGES.includes(language) ? language : "plaintext";
}

function getHighlighter() {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: [HIGHLIGHT_THEME],
    }).catch((error) => {
      highlighterPromise = undefined;
      throw error;
    });
  }

  return highlighterPromise;
}

function codeLanguagesIn(content) {
  const languages = new Set();
  const markdown = createMarkdownParser();

  for (const token of markdown.parse(content, {})) {
    if (token.type !== "fence") {
      continue;
    }

    const language = normalizeLanguage(token.info);
    if (language !== "plaintext") {
      languages.add(language);
    }
  }

  return [...languages];
}

async function loadCodeLanguages(highlighter, languages) {
  const loadedLanguages = new Set(highlighter.getLoadedLanguages());

  await Promise.all(
    languages
      .filter((language) => !loadedLanguages.has(language))
      .map((language) => highlighter.loadLanguage(language)),
  );
}

function renderCodeBlock(code, language, codeIndex, highlighter) {
  const languageLabel = language === "plaintext" ? "text" : language;
  const copyButton = `<button class="markdown-code-copy" type="button" data-code-index="${codeIndex}">Copy</button>`;
  let highlightedCode = "";

  if (highlighter) {
    try {
      highlightedCode = highlighter.codeToHtml(code, {
        lang: language,
        theme: HIGHLIGHT_THEME,
      });
    } catch {
      highlightedCode = "";
    }
  }

  if (!highlightedCode) {
    highlightedCode = `<pre><code class="language-${languageLabel}">${escapeHtml(code)}</code></pre>`;
  }

  return `<div class="markdown-code-block"><div class="markdown-code-toolbar"><span>${escapeHtml(languageLabel)}</span>${copyButton}</div>${highlightedCode}</div>`;
}

function createMarkdownParser(options = {}) {
  const markdown = new MarkdownIt({
    html: false,
    linkify: false,
    breaks: false,
    ...options,
  });

  markdown.disable("image");
  return markdown;
}

function createMarkdownRenderer(highlighter, codeBlocks) {
  return createMarkdownParser({
    highlight(code, info) {
      const language = normalizeLanguage(info);
      const codeIndex = codeBlocks.push(code) - 1;
      return renderCodeBlock(code, language, codeIndex, highlighter);
    },
  });
}

export async function renderMarkdown(content) {
  const normalizedContent = typeof content === "string" ? content : "";
  let highlighter = null;

  try {
    const languages = codeLanguagesIn(normalizedContent);
    if (languages.length > 0) {
      highlighter = await getHighlighter();
      await loadCodeLanguages(highlighter, languages);
    }
  } catch {
    highlighter = null;
  }

  const codeBlocks = [];
  const markdown = createMarkdownRenderer(highlighter, codeBlocks);

  return {
    codeBlocks,
    html: markdown.render(normalizedContent),
  };
}
