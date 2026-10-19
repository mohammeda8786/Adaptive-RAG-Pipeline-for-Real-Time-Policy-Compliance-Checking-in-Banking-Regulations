const state = { conversationId: null };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function formatAnswer(value, sources = []) {
  const sourceIds = new Set(sources.map((source) => source.id));
  return escapeHTML(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[\s*(S\d+)\s*\]/g, (citation, sourceId) => (
      sourceIds.has(sourceId)
        ? `<button type="button" class="answer-citation" data-citation="${sourceId}" aria-label="Show evidence for ${sourceId}">${citation}</button>`
        : citation
    ))
    .split(/\n{2,}/)
    .map((paragraph) => `<p>${paragraph.replace(/\n/g, "<br>")}</p>`)
    .join("");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === "object" ? body.detail?.message || body.detail || body.message : body;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return body;
}

function toast(message, isError = false) {
  const element = document.createElement("div");
  element.className = `toast${isError ? " error" : ""}`;
  element.textContent = message;
  $("#toastRegion").append(element);
  window.setTimeout(() => element.remove(), 3800);
}

function setBusy(busy) {
  $("#sendButton").disabled = busy;
  $("#messageInput").disabled = busy;
}

function newConversation() {
  state.conversationId = null;
  $("#messageList").replaceChildren();
  $("#welcomeState").hidden = false;
  $("#topbarView").textContent = "New conversation";
  $$(".conversation-row").forEach((row) => row.classList.remove("selected"));
  $("#messageInput").focus();
  closeSidebarOnMobile();
}

function sourceCards(sources = []) {
  if (!sources.length) return "";
  const entries = sources.map((source) => {
    const section = source.section
      ? `${source.chapter ? `${source.chapter}, ` : ""}Section ${source.section}`
      : source.chapter || source.reference;
    const metadata = [
      ["Source", source.document],
      ["Section", section],
      ["Clauses", source.clauses],
      ["Page", source.page || "Not available in extracted source"],
    ].filter(([label, value]) => (
      label === "Page"
        ? true
        : Array.isArray(value)
          ? value.length > 0
          : value !== undefined && value !== null && String(value).trim() !== ""
    ));
    const sourceReference = metadata.map(([label, value]) => (
      `<span class="source-metadata-item"><strong>${label}:</strong> ${escapeHTML(Array.isArray(value) ? value.join(", ") : value)}</span>`
    )).join("");
    return `
    <article class="source-card" data-source-id="${escapeHTML(source.id)}">
      <div class="source-card-head"><span class="source-id">${escapeHTML(source.id)}</span><span class="source-title">${escapeHTML(source.title || source.document)}</span></div>
      <div class="source-ref">${sourceReference}</div>
      <span class="source-evidence-label">Retrieved passage (as indexed)</span>
      <p class="source-excerpt">${escapeHTML(source.excerpt || "")}</p>
    </article>`;
  }).join("");
  return `<details class="source-section"><summary class="source-summary">${sources.length} retrieved ${sources.length === 1 ? "passage" : "passages"} · references and excerpts</summary><p class="source-note">S1, S2, etc. are chat references for these retrieved passages, not RBI clause numbers. Check the excerpt and section details below.</p>${entries}</details>`;
}

function actionButtons(message) {
  const messageId = Number(message.message_id || message.id) || "";
  const content = escapeHTML(message.content || message.answer || "");
  return `<div class="message-actions">
    <button type="button" data-action="copy" data-content="${content}" aria-label="Copy answer" title="Copy answer"><svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg></button>
    <button type="button" data-action="feedback" data-rating="helpful" data-id="${messageId}" aria-label="Helpful" title="Helpful"><svg viewBox="0 0 24 24"><path d="M7 10v11H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3ZM7 10l5-8 1.5 1a3 3 0 0 1 1 3.5L13 10h6a2 2 0 0 1 2 2l-1.5 7a2 2 0 0 1-2 1H7"/></svg></button>
    <button type="button" data-action="feedback" data-rating="not_helpful" data-id="${messageId}" aria-label="Not helpful" title="Not helpful"><svg viewBox="0 0 24 24"><path d="M7 14V3H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3ZM7 14l5 8 1.5-1a3 3 0 0 0 1-3.5L13 14h6a2 2 0 0 0 2-2l-1.5-7a2 2 0 0 0-2-1H7"/></svg></button>
    <button type="button" data-action="retry" aria-label="Retry response" title="Retry"><svg viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 9A7 7 0 0 1 18 6.5L20 12M4 12l2 5.5A7 7 0 0 0 18.4 15"/></svg></button>
  </div>`;
}

function appendMessage(message, options = {}) {
  $("#welcomeState").hidden = true;
  const role = message.role || (message.answer ? "assistant" : "user");
  const content = message.content ?? message.answer ?? "";
  const article = document.createElement("article");
  article.className = `message ${role === "user" ? "user" : "assistant"}`;
  article.dataset.messageId = message.message_id || message.id || "";
  if (role === "assistant") {
    article.innerHTML = `<div class="assistant-mark" aria-hidden="true">R</div><div class="message-bubble">${formatAnswer(content, message.sources)}</div>${message.sufficient_evidence === false ? '<div class="evidence-warning">Retrieved evidence was not sufficiently close to support a reliable conclusion. Treat these matches as leads for review, not confirmed requirements.</div>' : ""}${sourceCards(message.sources)}${actionButtons({ ...message, content })}`;
  } else {
    article.innerHTML = `<div class="message-bubble">${formatAnswer(content)}</div>`;
  }
  $("#messageList").append(article);
  if (options.scroll !== false) scrollToBottom();
  return article;
}

function addLoadingMessage() {
  const article = document.createElement("article");
  article.className = "message assistant";
  article.dataset.loading = "true";
  article.innerHTML = '<div class="assistant-mark" aria-hidden="true">R</div><div class="loading-bubble"><span class="typing-dots"><i></i><i></i><i></i></span><span>Searching cited RBI sources</span></div>';
  $("#welcomeState").hidden = true;
  $("#messageList").append(article);
  scrollToBottom();
  return article;
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    $("#conversationScroll").scrollTop = $("#conversationScroll").scrollHeight;
  });
}

async function sendMessage(rawText, options = {}) {
  const text = String(rawText || "").trim();
  if (!text || $("#sendButton").disabled) return;
  if (!options.retry) appendMessage({ role: "user", content: text });
  $("#messageInput").value = "";
  $("#messageInput").style.height = "auto";
  setBusy(true);
  const loading = addLoadingMessage();
  try {
    const result = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: state.conversationId, message: text }),
    });
    loading.remove();
    state.conversationId = result.conversation_id;
    if (result.report_setup) {
      appendMessage({ role: "assistant", content: "This chat is focused on RBI regulatory questions. I can still retrieve and explain relevant requirements with source citations." });
    } else {
      appendMessage(result);
    }
    $("#topbarView").textContent = text.length > 44 ? `${text.slice(0, 44)}…` : text;
    await loadConversations();
  } catch (error) {
    loading.remove();
    appendMessage({ role: "assistant", content: `I couldn’t complete that request. ${error.message}` });
    toast(error.message, true);
  } finally {
    setBusy(false);
    $("#messageInput").focus();
  }
}

async function loadConversations() {
  try {
    const { conversations } = await api("/api/conversations?limit=30");
    const list = $("#conversationList");
    list.replaceChildren();
    if (!conversations.length) {
      list.innerHTML = '<div class="empty-sidebar">Your conversations will appear here.</div>';
      return;
    }
    for (const conversation of conversations) {
      const item = document.createElement("div");
      item.className = "conversation-item";

      const button = document.createElement("button");
      button.className = `conversation-row${conversation.id === state.conversationId ? " selected" : ""}`;
      button.type = "button";
      button.dataset.c
      
      onversationId = conversation.id;
      button.innerHTML = `<svg viewBox="0 0 24 24"><path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.7-3.3A7.5 7.5 0 1 1 20 11.5Z"/></svg><span>${escapeHTML(conversation.title)}</span>`;

      const deleteButton = document.createElement("button");
      deleteButton.className = "conversation-delete";
      deleteButton.type = "button";
      deleteButton.dataset.deleteConversation = conversation.id;
      deleteButton.setAttribute("aria-label", `Delete conversation: ${conversation.title}`);
      deleteButton.title = "Delete conversation";
      deleteButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m4 4v6m6-6v6"/></svg>';

      item.append(button, deleteButton);
      list.append(item);
    }
  } catch {
    $("#conversationList").innerHTML = '<div class="empty-sidebar">History is unavailable.</div>';
  }
}

async function deleteConversation(conversationId) {
  const item = $(`[data-delete-conversation="${CSS.escape(conversationId)}"]`)?.closest(".conversation-item");
  const title = item?.querySelector(".conversation-row span")?.textContent || "this conversation";
  if (!window.confirm(`Delete "${title}" and its chat history? This cannot be undone.`)) return;

  try {
    await api(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
    if (state.conversationId === conversationId) newConversation();
    await loadConversations();
    toast("Conversation deleted.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function openConversation(conversationId) {
  try {
    const result = await api(`/api/conversations/${encodeURIComponent(conversationId)}`);
    state.conversationId = conversationId;
    $("#messageList").replaceChildren();
    $("#welcomeState").hidden = true;
    $("#topbarView").textContent = result.conversation.title;
    for (const message of result.messages) appendMessage(message, { scroll: false });
    await loadConversations();
    scrollToBottom();
    closeSidebarOnMobile();
  } catch (error) {
    toast(error.message, true);
  }
}

function closeSidebarOnMobile() {
  $("#sidebar").classList.remove("mobile-open");
  document.body.classList.remove("nav-open");
}

function openSidebarOnMobile() {
  $("#sidebar").classList.add("mobile-open");
  document.body.classList.add("nav-open");
}

async function refreshStatus() {
  try {
    const result = await api("/api/health");
    const ready = result.retrieval.ready;
    $("#statusDot").classList.toggle("ready", ready);
    $("#statusDot").classList.toggle("error", !ready);
    $("#workspaceStatus").textContent = ready ? `${result.retrieval.source_count} RBI sources indexed` : "RBI index unavailable";
    $("#modelLabel").textContent = result.llm_configured ? result.llm_model : "Evidence mode";
    $(".model-indicator").classList.toggle("ready", ready);
  } catch {
    $("#workspaceStatus").textContent = "API unavailable";
  }
}

$("#sendButton").addEventListener("click", () => sendMessage($("#messageInput").value));
$("#messageInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage(event.currentTarget.value);
  }
});
$("#messageInput").addEventListener("input", (event) => {
  event.currentTarget.style.height = "auto";
  event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 150)}px`;
});
$("#newChat").addEventListener("click", newConversation);
$("#mobileMenu").addEventListener("click", openSidebarOnMobile);
$("#collapseSidebar").addEventListener("click", () => {
  $("#sidebar").classList.toggle("is-collapsed");
  $(".app-shell").classList.toggle("sidebar-collapsed");
});
$("#refreshConversations").addEventListener("click", loadConversations);

document.addEventListener("click", async (event) => {
  const citation = event.target.closest(".answer-citation[data-citation]");
  if (citation) {
    const message = citation.closest(".message");
    const sources = message?.querySelector(".source-section");
    const card = sources?.querySelector(`[data-source-id="${CSS.escape(citation.dataset.citation)}"]`);
    if (sources && card) {
      sources.open = true;
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("source-card-highlight");
      window.setTimeout(() => card.classList.remove("source-card-highlight"), 1400);
    }
    return;
  }
  const deleteButton = event.target.closest("[data-delete-conversation]");
  if (deleteButton) {
    await deleteConversation(deleteButton.dataset.deleteConversation);
    return;
  }
  const conversation = event.target.closest(".conversation-row[data-conversation-id]");
  if (conversation) {
    openConversation(conversation.dataset.conversationId);
    return;
  }
  const action = event.target.closest("[data-action]");
  if (!action) return;
  if (action.dataset.action === "copy") {
    try { await navigator.clipboard.writeText(action.dataset.content); toast("Answer copied."); }
    catch { toast("Clipboard access is not available in this browser.", true); }
  } else if (action.dataset.action === "retry") {
    const lastUserMessage = [...$$(".message.user .message-bubble")].at(-1)?.textContent || "";
    if (lastUserMessage) sendMessage(lastUserMessage, { retry: true });
  } else if (action.dataset.action === "feedback") {
    if (!action.dataset.id) { toast("Feedback is available after this response is saved."); return; }
    try {
      await api(`/api/messages/${encodeURIComponent(action.dataset.id)}/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating: action.dataset.rating }),
      });
      action.classList.add("chosen");
      toast("Feedback saved.");
    } catch (error) { toast(error.message, true); }
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    newConversation();
  }
  if (event.key === "Escape") closeSidebarOnMobile();
});

document.addEventListener("click", (event) => {
  if (document.body.classList.contains("nav-open") && !event.target.closest(".sidebar, #mobileMenu")) closeSidebarOnMobile();
});

async function init() {
  await refreshStatus();
  await loadConversations();
  $("#messageInput").focus();
}

init();