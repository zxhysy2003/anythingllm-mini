import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SourceList from "./SourceList.vue";

describe("SourceList", () => {
  it("shows safe source fields, expands text, and locates the selected message", async () => {
    const fullText = "The complete persisted source text is longer than its preview.";
    const wrapper = mount(SourceList, {
      props: {
        lowScoreThreshold: 0.8,
        messageId: "assistant-1",
        metrics: { has_context: true },
        sources: [
          {
            chunk_index: 2,
            document_id: "private-id",
            original_filename: "guide.txt",
            path: "/private/storage/guide.txt",
            score: 0.72,
            text: fullText,
          },
        ],
      },
    });

    await wrapper.find("section > button").trigger("click");

    expect(wrapper.text()).toContain("guide.txt");
    expect(wrapper.text()).toContain("Chunk 2");
    expect(wrapper.text()).toContain("Score 0.720");
    expect(wrapper.text()).toContain("Low confidence");
    expect(wrapper.text()).not.toContain("/private/storage");
    expect(wrapper.text()).not.toContain("private-id");

    await wrapper.findAll("button")[1].trigger("click");

    expect(wrapper.text()).toContain(fullText);
    expect(wrapper.emitted("locate")).toEqual([["assistant-1"]]);
  });

  it("explains query-mode answers without eligible context", async () => {
    const wrapper = mount(SourceList, {
      props: {
        messageId: "assistant-2",
        metrics: { query_refused: true },
      },
    });

    await wrapper.find("section > button").trigger("click");

    expect(wrapper.text()).toContain("No source met this workspace's retrieval threshold.");
  });
});
