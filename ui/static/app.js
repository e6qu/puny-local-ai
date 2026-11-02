"use strict";
// The little the pages need beyond HTMX: chat, which streams token by token,
// and keeping a job's output scrolled to its end while it runs.

const el = (tag, props = {}, ...kids) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const kid of kids) node.append(kid);
  return node;
};

// ------------------------------------------------------------ job output
function follow(root) {
  for (const pre of root.querySelectorAll("pre[data-follow]")) pre.scrollTop = pre.scrollHeight;
}
document.addEventListener("DOMContentLoaded", () => follow(document));
document.addEventListener("htmx:afterSettle", (event) => follow(event.target));

// ------------------------------------------------------------ chat
let history = [];

function render(text, into) {
  // Fenced code blocks become <pre>; everything else stays plain text.
  into.replaceChildren();
  text.split(/```[^\n]*\n?/).forEach((part, i) => {
    into.append(i % 2 ? el("pre", {}, el("code", {textContent: part})) : document.createTextNode(part));
  });
}

async function send(event) {
  event.preventDefault();
  const prompt = document.getElementById("prompt");
  const log = document.getElementById("log");
  const button = document.getElementById("send");
  const text = prompt.value.trim();
  if (!text) return;
  prompt.value = "";
  history.push({role: "user", content: text});
  log.append(el("div", {className: "msg user", textContent: text}));
  const thoughts = el("div");
  const thinking = el("details", {className: "thinking", hidden: true}, el("summary", {textContent: "Thinking"}), thoughts);
  const answer = el("div");
  const bubble = el("div", {className: "msg assistant"}, thinking, answer);
  log.append(bubble);
  button.disabled = true;
  let content = "", reasoning = "";
  try {
    const response = await fetch("/api/chat", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({messages: history, stream: true,
        chat_template_kwargs: {enable_thinking: document.getElementById("think").checked}}),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error?.message || body.error || `HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ") || line === "data: [DONE]") continue;
        const delta = JSON.parse(line.slice(6)).choices?.[0]?.delta || {};
        if (delta.reasoning) { reasoning += delta.reasoning; thinking.hidden = false; thoughts.textContent = reasoning; }
        if (delta.content) { content += delta.content; render(content, answer); }
      }
      bubble.scrollIntoView({block: "end"});
    }
    history.push({role: "assistant", content});
  } catch (e) {
    bubble.className = "msg error";
    answer.textContent = e.message;
    history.pop();
  } finally {
    button.disabled = false;
    prompt.focus();
  }
}

// The chat page may arrive through a boosted link, after DOMContentLoaded.
function setUpChat() {
  const form = document.getElementById("ask");
  if (!form || form.dataset.ready) return;
  form.dataset.ready = "1";
  history = [];
  form.addEventListener("submit", send);
  document.getElementById("prompt").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  document.getElementById("clear").addEventListener("click", () => {
    history = [];
    document.getElementById("log").replaceChildren();
  });
}
document.addEventListener("DOMContentLoaded", setUpChat);
document.addEventListener("htmx:afterSettle", setUpChat);
