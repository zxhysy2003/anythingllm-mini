import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import MessageMarkdown from "./MessageMarkdown.vue";

async function waitForRender(wrapper) {
  await vi.waitFor(() => {
    expect(wrapper.find(".message-markdown").html()).not.toBe(
      '<div class="message-markdown"></div>',
    );
  });
}

describe("MessageMarkdown", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders lists, tables, links, and highlighted fenced code", async () => {
    const wrapper = mount(MessageMarkdown, {
      props: {
        content:
          "- first\n- second\n\n| Name | Value |\n| --- | --- |\n| Item | 42 |\n\n[Docs](https://example.com)\n\n```js\nconst answer = 42;\n```",
      },
    });

    await waitForRender(wrapper);

    expect(wrapper.find("ul").exists()).toBe(true);
    expect(wrapper.find("table").exists()).toBe(true);
    expect(wrapper.find('a[href="https://example.com"]').exists()).toBe(true);
    expect(wrapper.find("pre.shiki").exists()).toBe(true);
    expect(wrapper.find(".markdown-code-copy").text()).toBe("Copy");
  });

  it("highlights tilde and longer backtick fences", async () => {
    const wrapper = mount(MessageMarkdown, {
      props: {
        content:
          "~~~js\nconst tildeFence = true;\n~~~\n\n````js\nconst longFence = true;\n````",
      },
    });

    await waitForRender(wrapper);

    expect(wrapper.findAll("pre.shiki")).toHaveLength(2);
  });

  it("does not render raw HTML or remote Markdown images", async () => {
    const wrapper = mount(MessageMarkdown, {
      props: {
        content:
          '<img src="https://example.com/tracker.png" onerror="window.bad = true">\n\n![remote](https://example.com/image.png)',
      },
    });

    await waitForRender(wrapper);

    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.text()).toContain("tracker.png");
    expect(wrapper.text()).toContain("remote");
  });

  it("copies the original code and shows success feedback", async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const wrapper = mount(MessageMarkdown, {
      props: { content: "```python\nprint('hello')\n```" },
    });

    await waitForRender(wrapper);
    await wrapper.find(".markdown-code-copy").trigger("click");

    expect(writeText).toHaveBeenCalledWith("print('hello')\n");
    expect(wrapper.find(".markdown-code-copy").text()).toBe("Copied");
  });

  it("shows failure feedback when clipboard access fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const wrapper = mount(MessageMarkdown, {
      props: { content: "```text\ncopy me\n```" },
    });

    await waitForRender(wrapper);
    await wrapper.find(".markdown-code-copy").trigger("click");

    expect(wrapper.find(".markdown-code-copy").text()).toBe("Copy failed");
  });
});
