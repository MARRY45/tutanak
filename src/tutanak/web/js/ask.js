// "Ask your meetings": a question box and an answer with links to the cited moments.

import { api } from "./api.js";
import { fmtDate, t } from "./i18n.js";
import { clear, fmtTs, h, icon } from "./util.js";

const KIND_ICON = { transcript: "mic", slide: "slides", note: "note" };

export function sourceRow(passage, { showMeeting, onOpen, number, snippet }) {
  const where = [showMeeting ? passage.meeting_title : null, fmtTs(passage.start)].filter(Boolean).join(" · ");
  return h(
    "a",
    {
      class: "source-row",
      href: `#/m/${passage.meeting_id}?t=${Math.floor(passage.start)}`,
      onclick: (event) => {
        if (onOpen) {
          event.preventDefault();
          onOpen(passage);
        }
      },
    },
    number ? h("span", { class: "cite-n" }, `[${number}]`) : null,
    h("span", { class: "kind", title: t(`kind_${passage.kind}`) }, icon(KIND_ICON[passage.kind] || "note", 15)),
    h(
      "span",
      { class: "source-body" },
      h("span", { class: "source-meta" }, where, showMeeting ? ` · ${fmtDate(passage.meeting_started_at)}` : ""),
      h("span", { class: "source-text" }, snippet || passage.snippet || passage.text),
    ),
  );
}

// Replaces [n] in the answer with a link to the cited passage.
function answerNodes(answer, citations, onOpen) {
  const byNumber = new Map(citations.map((c) => [c.n, c]));
  const nodes = [];
  let last = 0;
  for (const match of answer.matchAll(/\[(\d+)\]/g)) {
    const cited = byNumber.get(Number(match[1]));
    if (!cited) continue;
    nodes.push(answer.slice(last, match.index));
    nodes.push(
      h(
        "a",
        {
          class: "cite",
          href: `#/m/${cited.meeting_id}?t=${Math.floor(cited.start)}`,
          title: `${cited.meeting_title} · ${fmtTs(cited.start)}`,
          onclick: (event) => {
            if (onOpen) {
              event.preventDefault();
              onOpen(cited);
            }
          },
        },
        match[1],
      ),
    );
    last = match.index + match[0].length;
  }
  nodes.push(answer.slice(last));
  return nodes;
}

export function renderAnswer(target, result, { showMeeting = true, onOpen } = {}) {
  clear(target);
  if (!result.passages.length) {
    target.append(h("p", { class: "muted" }, t("ask_nothing")));
    return;
  }
  if (result.answer) {
    target.append(h("div", { class: "answer" }, answerNodes(result.answer, result.citations, onOpen)));
  } else {
    target.append(h("p", { class: "notice" }, t("ask_no_model")));
  }
  const sources = result.answer && result.citations.length ? result.citations : result.passages;
  target.append(
    h("h4", { class: "label" }, t("sources")),
    h(
      "div",
      { class: "source-list" },
      sources.map((p) => sourceRow(p, { showMeeting, onOpen, number: result.answer ? p.n : null })),
    ),
  );
}

export function askBox({ meetingId = null, showMeeting = true, onOpen, initial = "" } = {}) {
  const input = h("input", { type: "text", class: "ask-input", placeholder: t("ask_placeholder"), value: initial });
  const button = h("button", { class: "button primary", type: "submit" }, icon("spark", 16), t("ask_button"));
  const output = h("div", { class: "ask-output" });
  const form = h("form", { class: "ask-form" }, input, button);

  async function run() {
    const question = input.value.trim();
    if (question.length < 2) return;
    button.disabled = true;
    clear(output).append(h("p", { class: "muted pulse" }, t("asking")));
    try {
      const result = await api.post("/ask", { question, meeting_id: meetingId });
      renderAnswer(output, result, { showMeeting, onOpen });
    } catch (error) {
      clear(output).append(h("p", { class: "notice error" }, error.message));
    } finally {
      button.disabled = false;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run();
  });
  const el = h("div", { class: "ask" }, form, output);
  return { el, run, input };
}
