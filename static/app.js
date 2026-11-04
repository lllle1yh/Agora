"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  after: 0,
  roles: [],
  messages: [],
  files: [],
  selected: null,
  paused: false,
  evalReady: false,
  finalReady: false,
  topic: "",
  background: "",
  archives: [],
  roleNodes: new Map(),
  gridNodes: new Map(),
  editorRoles: [],
  editorEvaluators: [],
  sideMessages: [],
  tab: "feed",
  rightTab: "pane",
  filePath: "",
  polling: false,
  alertTimer: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function showAlert(message, kind = "error") {
  const box = $("alert");
  box.textContent = message;
  box.className = `alert${kind === "ok" ? " ok" : ""}`;
  if (state.alertTimer) clearTimeout(state.alertTimer);
  state.alertTimer = setTimeout(() => box.classList.add("hidden"), 8000);
}

function roleColor(id) {
  const role = state.roles.find((item) => item.id === id);
  return role ? role.color : "#8493b2";
}

function badgeClass(status) {
  if (status === "输出中") return "live";
  if (status === "未启动") return "off";
  return "";
}

function statusText() {
  if (state.paused) return "已暂停";
  if (state.finalReady) return "方案已生成";
  if (state.evalReady) return "评估者已就绪";
  if (state.roles.some((role) => role.status === "输出中")) return "讨论中";
  return "空闲";
}

function renderStatus() {
  $("role-count").textContent = `${state.roles.length} 角色`;
  $("message-count").textContent = `${state.messages.length} 消息`;
  const evalBadge = $("eval-state");
  evalBadge.textContent = statusText();
  evalBadge.className = `badge ${state.finalReady || state.evalReady ? "ok" : state.paused ? "live" : ""}`;
  $("pause-btn").textContent = state.paused ? "继续" : "暂停";
  $("pause-btn").classList.toggle("danger", state.paused);
  $("topic-mini").textContent = state.topic ? state.topic.slice(0, 60) : "";
}

function renderRoleList() {
  const box = $("roles");
  box.replaceChildren();
  state.roleNodes.clear();
  if (!state.roles.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "还没有角色。点「新建 / 设置」生成角色。";
    box.append(empty);
    return;
  }
  for (const role of state.roles) {
    const node = document.createElement("div");
    node.className = `role${role.id === state.selected ? " selected" : ""}`;
    node.addEventListener("click", () => selectRole(role.id));
    const dot = document.createElement("span");
    dot.className = "role-dot";
    dot.style.background = role.color;
    const main = document.createElement("div");
    main.className = "role-main";
    const name = document.createElement("div");
    name.className = "role-name";
    name.textContent = role.name;
    const sub = document.createElement("div");
    sub.className = "role-sub";
    const snippet = (role.pane || "").split("\n").filter(Boolean).slice(-1)[0] || "等待输出";
    sub.textContent = `${role.status} · ${snippet.slice(0, 32)}`;
    main.append(name, sub);
    node.append(dot, main);
    box.append(node);
    state.roleNodes.set(role.id, { node, sub });
  }
}

function renderRoleGrid() {
  const box = $("role-grid");
  if (!state.roles.length) {
    box.replaceChildren();
    box.dataset.ready = "";
    box.dataset.signature = "";
    state.gridNodes.clear();
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "还没有角色。点「新建 / 设置」生成角色。";
    box.append(empty);
    return;
  }
  const signature = state.roles.map((role) => role.id).join(",");
  if (box.dataset.signature !== signature) {
    box.dataset.ready = "";
    box.dataset.signature = signature;
    state.gridNodes.clear();
  }
  if (!box.dataset.ready) {
    box.replaceChildren();
    for (const role of state.roles) {
      const card = document.createElement("div");
      card.className = "role-card";
      card.dataset.role = role.id;
      card.addEventListener("click", () => selectRole(role.id));
      const head = document.createElement("div");
      head.className = "role-card-head";
      const name = document.createElement("strong");
      name.style.color = role.color;
      const badge = document.createElement("span");
      badge.className = "badge";
      head.append(name, badge);
      const pre = document.createElement("pre");
      card.append(head, pre);
      box.append(card);
      state.gridNodes.set(role.id, { card, badge, name, pre });
    }
    box.dataset.ready = "1";
  }
  for (const role of state.roles) {
    const node = state.gridNodes.get(role.id);
    if (!node) continue;
    node.card.classList.toggle("active", role.id === state.selected);
    node.name.textContent = role.name;
    node.badge.textContent = role.status;
    node.badge.className = `badge ${badgeClass(role.status)}`;
    node.pre.textContent = role.pane || "等待输出…";
  }
}

function renderMessages() {
  const feed = $("feed");
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;
  if (!feed.dataset.ready) {
    feed.replaceChildren();
    feed.dataset.ready = "1";
  }
  const existing = new Set([...feed.querySelectorAll(".message")].map((node) => node.dataset.seq));
  for (const message of state.messages) {
    if (existing.has(String(message.seq))) continue;
    const node = document.createElement("div");
    node.className = "message";
    node.dataset.seq = String(message.seq);
    const head = document.createElement("div");
    head.className = "message-head";
    const role = document.createElement("span");
    role.className = "message-role";
    role.style.color = message.role === "moderator" ? "#f6e05e" : roleColor(message.role);
    role.textContent = message.name || message.role;
    const kind = document.createElement("span");
    kind.className = "message-kind";
    kind.textContent = message.kind || "message";
    const time = document.createElement("span");
    time.textContent = (message.ts || "").replace("T", " ").slice(0, 19);
    head.append(role, kind, time);
    const text = document.createElement("div");
    text.className = "message-text";
    text.textContent = message.text || "";
    node.append(head, text);
    feed.append(node);
  }
  if (!state.messages.length) {
    if (!feed.querySelector(".empty")) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "还没有讨论消息。先新建讨论，或等待角色输出。";
      feed.append(empty);
    }
  } else {
    const empty = feed.querySelector(".empty");
    if (empty) empty.remove();
  }
  if (nearBottom) feed.scrollTop = feed.scrollHeight;
}

function renderDetail() {
  const role = state.roles.find((item) => item.id === state.selected);
  if (!role) return;
  $("detail-name").textContent = role.name;
  $("detail-status").textContent = `${role.short || ""} · ${role.status} · ${role.updated_at || "无更新"}`;
  const badge = $("detail-badge");
  badge.textContent = role.status;
  badge.className = `badge ${badgeClass(role.status)}`;
  const pane = $("detail-pane");
  const nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80;
  pane.textContent = role.pane || "等待角色输出…";
  if (nearBottom) pane.scrollTop = pane.scrollHeight;
}

function renderSidechat() {
  const box = $("sidechat");
  box.replaceChildren();
  if (!state.sideMessages.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "旁路对话独立于主讨论。可以问它总结、判断或给建议。";
    box.append(empty);
    return;
  }
  for (const item of state.sideMessages) {
    const node = document.createElement("div");
    node.className = `side-msg ${item.role === "user" ? "user" : "assistant"}`;
    const head = document.createElement("div");
    head.className = "side-head";
    head.textContent = item.role === "user" ? "你" : "旁路顾问";
    const text = document.createElement("div");
    text.className = "side-text";
    text.textContent = item.text || "";
    node.append(head, text);
    box.append(node);
  }
  box.scrollTop = box.scrollHeight;
}

function renderFiles() {
  const box = $("files");
  box.replaceChildren();
  if (!state.files.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "暂无文件";
    box.append(empty);
    return;
  }
  const groups = new Map();
  for (const file of state.files) {
    const group = file.path.split("/")[0] || "其他";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(file);
  }
  for (const [group, files] of groups) {
    const section = document.createElement("div");
    section.className = "file-group";
    const head = document.createElement("div");
    head.className = "file-group-head";
    head.textContent = group;
    section.append(head);
    for (const file of files) {
      const node = document.createElement("div");
      node.className = `file-item${file.path === state.filePath ? " active" : ""}`;
      node.textContent = file.path.slice(group.length + 1);
      node.title = `${file.size} bytes · ${file.mtime}`;
      node.addEventListener("click", () => openFile(file.path));
      section.append(node);
    }
    box.append(section);
  }
}

async function openFile(path) {
  state.filePath = path;
  renderFiles();
  try {
    const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
    $("file-preview").textContent = data.text || "(空文件)";
  } catch (error) {
    $("file-preview").textContent = `读取失败：${error.message}`;
  }
}

function selectRole(id) {
  state.selected = id;
  renderRoleList();
  renderRoleGrid();
  renderDetail();
}

function switchTab(name) {
  state.tab = name;
  for (const tab of document.querySelectorAll(".tab")) tab.classList.toggle("active", tab.dataset.tab === name);
  for (const panel of document.querySelectorAll(".tab-panel")) panel.classList.toggle("active", panel.id === `tab-${name}`);
}

function switchRightTab(name) {
  state.rightTab = name;
  for (const tab of document.querySelectorAll(".rtab")) tab.classList.toggle("active", tab.dataset.rtab === name);
  for (const panel of document.querySelectorAll(".rtab-panel")) panel.classList.toggle("active", panel.id === `rtab-${name}`);
  if (name === "side") loadSidechat();
}

async function poll() {
  if (state.polling) return;
  state.polling = true;
  try {
    const data = await api(`/api/state?after=${state.after}`);
    state.roles = data.roles || [];
    state.files = data.files || [];
    state.evalReady = Boolean(data.evaluator_ready);
    state.finalReady = Boolean(data.final_ready);
    state.paused = Boolean(data.paused);
    state.topic = data.topic || state.topic;
    if (!state.selected && state.roles.length) state.selected = state.roles[0].id;
    const newMessages = data.messages || [];
    if (newMessages.length) {
      state.messages.push(...newMessages);
      state.after = Math.max(state.after, ...newMessages.map((item) => item.seq));
      renderMessages();
    }
    if (!state.messages.length) renderMessages();
    renderRoleList();
    renderRoleGrid();
    renderDetail();
    renderStatus();
    renderFiles();
  } catch (error) {
    console.error("poll", error);
  } finally {
    state.polling = false;
  }
}

async function loadSidechat() {
  try {
    const data = await api("/api/side-chat");
    state.sideMessages = data.messages || [];
    renderSidechat();
  } catch (error) {
    console.error("sidechat", error);
  }
}

async function sendSidechat(event) {
  event.preventDefault();
  const input = $("side-input");
  const text = input.value.trim();
  if (!text) return;
  state.sideMessages.push({ role: "user", text, ts: new Date().toISOString() });
  renderSidechat();
  input.value = "";
  try {
    const data = await api("/api/side-chat", {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    state.sideMessages = data.history || state.sideMessages;
    renderSidechat();
  } catch (error) {
    showAlert(`旁路对话失败：${error.message}`);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const text = $("message").value.trim();
  if (!text) return;
  try {
    const item = await api("/api/message", { method: "POST", body: JSON.stringify({ text, role: "moderator" }) });
    state.messages.push(item);
    state.after = Math.max(state.after, item.seq);
    $("message").value = "";
    renderMessages();
  } catch (error) {
    showAlert(`发送失败：${error.message}`);
  }
}

async function togglePause() {
  try {
    const data = await api("/api/pause", { method: "POST", body: JSON.stringify({ paused: !state.paused }) });
    state.paused = Boolean(data.paused);
    renderStatus();
  } catch (error) {
    showAlert(`暂停失败：${error.message}`);
  }
}

async function stopDiscussion() {
  if (!confirm("停止当前讨论？角色进程会被结束，记录保留。")) return;
  try {
    await api("/api/discussion/stop", { method: "POST", body: "{}" });
    showAlert("讨论已停止", "ok");
  } catch (error) {
    showAlert(`停止失败：${error.message}`);
  }
}

async function archiveDiscussion() {
  if (!confirm("归档当前讨论并清空当前页面？归档后可在左侧历史讨论中查看。")) return;
  try {
    const data = await api("/api/archive", { method: "POST", body: "{}" });
    state.messages = [];
    state.after = 0;
    state.topic = "";
    state.background = "";
    state.selected = null;
    $("feed").replaceChildren();
    $("feed").dataset.ready = "";
    renderMessages();
    await loadHistory();
    await poll();
    showAlert(`已归档到 ${data.path}，当前页面已清空`, "ok");
  } catch (error) {
    showAlert(`归档失败：${error.message}`);
  }
}

async function loadHistory() {
  try {
    const data = await api("/api/archives");
    state.archives = data.archives || [];
    renderHistory();
  } catch (error) {
    console.error("history", error);
  }
}

function renderHistory() {
  const box = $("history");
  box.replaceChildren();
  if (!state.archives.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "暂无归档";
    box.append(empty);
    return;
  }
  for (const item of state.archives) {
    const node = document.createElement("div");
    node.className = "history-item";
    const title = document.createElement("div");
    title.className = "history-title";
    title.textContent = item.topic || item.id;
    const meta = document.createElement("div");
    meta.className = "history-meta";
    meta.textContent = `${item.created_at || ""} · ${item.messages || 0} 条`;
    node.append(title, meta);
    node.addEventListener("click", () => openArchive(item.id));
    box.append(node);
  }
}

async function openArchive(id) {
  try {
    const data = await api(`/api/archive/${encodeURIComponent(id)}`);
    $("archive-title").textContent = data.topic || data.meta?.topic || id;
    $("archive-meta").textContent = `${data.meta?.created_at || ""} · ${data.messages?.length || 0} 条消息`;
    const body = $("archive-body");
    body.replaceChildren();
    if (data.background) {
      const bg = document.createElement("pre");
      bg.className = "archive-background";
      bg.textContent = data.background;
      body.append(bg);
    }
    for (const message of data.messages || []) {
      const node = document.createElement("div");
      node.className = "archive-message";
      const head = document.createElement("div");
      head.className = "archive-message-head";
      head.textContent = `${message.name || message.role} · ${message.ts || ""}`;
      const text = document.createElement("div");
      text.className = "archive-message-text";
      text.textContent = message.text || "";
      node.append(head, text);
      body.append(node);
    }
    $("archive-modal").classList.remove("hidden");
  } catch (error) {
    showAlert(`读取归档失败：${error.message}`);
  }
}

function closeArchive() {
  $("archive-modal").classList.add("hidden");
}

function updateBackgroundStatus() {
  const text = $("setup-background").value || "";
  const status = $("background-status");
  if (status) status.textContent = text ? `已加载 ${text.length} 字符` : "背景资料为空";
}

async function saveBackgroundDraft() {
  try {
    await api("/api/background", { method: "POST", body: JSON.stringify({ text: $("setup-background").value }) });
  } catch (error) {
    console.error("background save", error);
  }
}

async function handleFiles(files) {
  const zone = $("drop-zone");
  const background = $("setup-background");
  for (const file of files) {
    if (file.size > 2 * 1024 * 1024) {
      showAlert(`${file.name} 超过 2MB，跳过`);
      continue;
    }
    try {
      const text = await file.text();
      const header = `

===== ${file.name} =====
`;
      background.value += header + text;
      state.background = background.value;
      zone.textContent = `已加入 ${file.name}（${Math.ceil(file.size / 1024)} KiB）`;
    } catch (error) {
      showAlert(`读取 ${file.name} 失败：${error.message}`);
    }
  }
  updateBackgroundStatus();
  await saveBackgroundDraft();
}

async function exportDiscussion() {
  try {
    const data = await api("/api/export");
    const blob = new Blob([data.text], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = data.filename;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    showAlert(`导出失败：${error.message}`);
  }
}

function roleEditorRow(role, index, listName) {
  const list = state[listName];
  const row = document.createElement("div");
  row.className = "editor-row";
  const top = document.createElement("div");
  top.className = "editor-row-top";
  const name = document.createElement("input");
  name.value = role.name || (listName === "editorEvaluators" ? `评估者 ${index + 1}` : `角色 ${index + 1}`);
  name.placeholder = listName === "editorEvaluators" ? "评估者名" : "角色名";
  name.addEventListener("input", () => { list[index].name = name.value; });
  const color = document.createElement("input");
  color.type = "color";
  color.value = role.color || (listName === "editorEvaluators" ? "#f6e05e" : "#6ea8ff");
  color.addEventListener("input", () => { list[index].color = color.value; });
  const badge = document.createElement("span");
  badge.className = "editor-badge";
  badge.textContent = listName === "editorEvaluators" ? "评估者" : "讨论者";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn small ghost";
  remove.textContent = "删除";
  remove.addEventListener("click", () => {
    list.splice(index, 1);
    renderEditor();
  });
  top.append(name, color, badge, remove);

  const stance = document.createElement("textarea");
  stance.rows = 2;
  stance.value = role.stance || "";
  stance.placeholder = listName === "editorEvaluators"
    ? "评估标准：它关注什么、如何判断是否值得推进"
    : "立场：它怎么看问题，和其他角色有什么分歧";
  stance.addEventListener("input", () => { list[index].stance = stance.value; });

  const goal = document.createElement("input");
  goal.value = role.goal || "";
  goal.placeholder = listName === "editorEvaluators" ? "评估目标（可选）" : "目标：它在讨论中要达成什么（可选）";
  goal.addEventListener("input", () => { list[index].goal = goal.value; });

  row.append(top, stance, goal);
  return row;
}

function renderEditorList(boxId, listName, emptyText) {
  const box = $(boxId);
  box.replaceChildren();
  const list = state[listName];
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = emptyText;
    box.append(empty);
    return;
  }
  list.forEach((role, index) => box.append(roleEditorRow(role, index, listName)));
}

function renderEditor() {
  renderEditorList("role-editor", "editorRoles", "还没有讨论角色。点「调用模型生成」或「+ 讨论角色」。");
  renderEditorList("evaluator-editor", "editorEvaluators", "还没有评估者。点「+ 评估者」添加。");
}

function collectRoles() {
  const discussants = state.editorRoles.map((role, index) => ({
    name: role.name || `角色 ${index + 1}`,
    stance: role.stance || "",
    goal: role.goal || "",
    color: role.color || "#6ea8ff",
    evaluator: false,
    model: role.model || "",
  }));
  const evaluators = state.editorEvaluators.map((role, index) => ({
    name: role.name || `评估者 ${index + 1}`,
    stance: role.stance || "",
    goal: role.goal || "",
    color: role.color || "#f6e05e",
    evaluator: true,
    model: role.model || "",
  }));
  return [...discussants, ...evaluators];
}

function fillSettings(data) {
  $("setup-provider").value = data.provider || "openai";
  $("setup-base-url").value = data.base_url || "";
  $("setup-model").value = data.model || "";
  $("setup-api-key").value = "";
  $("setup-topic").value = data.topic || "";
  $("setup-background").value = data.background || "";
  state.background = data.background || "";
  updateBackgroundStatus();
  const roles = (data.roles || []).map((role) => ({
    name: role.name || "",
    stance: role.stance || "",
    goal: role.goal || "",
    color: role.color || "#6ea8ff",
    evaluator: Boolean(role.evaluator),
    model: role.model || "",
  }));
  state.editorRoles = roles.filter((role) => !role.evaluator);
  state.editorEvaluators = roles.filter((role) => role.evaluator);
  $("setup-role-count").value = String(state.editorRoles.length || 6);
  $("setup-evaluator-count").value = String(state.editorEvaluators.length || 0);
  renderEditor();
}

async function openSetup() {
  try {
    const data = await api("/api/settings");
    fillSettings(data);
    $("setup-modal").classList.remove("hidden");
  } catch (error) {
    showAlert(`读取配置失败：${error.message}`);
  }
}

function closeSetup() {
  $("setup-modal").classList.add("hidden");
}

function settingsPayload() {
  return {
    provider: $("setup-provider").value,
    base_url: $("setup-base-url").value.trim(),
    model: $("setup-model").value.trim(),
    api_key: $("setup-api-key").value.trim(),
  };
}

async function testModel() {
  const result = $("test-model-result");
  result.textContent = "测试中…";
  result.className = "muted";
  try {
    const data = await api("/api/model/test", { method: "POST", body: JSON.stringify(settingsPayload()) });
    result.textContent = `OK · ${data.latency_ms}ms${data.models?.length ? ` · ${data.models.length} models` : ""}`;
    result.className = "test-ok";
  } catch (error) {
    result.textContent = `失败：${error.message}`;
    result.className = "test-bad";
  }
}

async function generateRoles() {
  const topic = $("setup-topic").value.trim();
  if (!topic) {
    showAlert("先填写讨论主题");
    return;
  }
  const count = Math.max(1, Math.min(12, Number($("setup-role-count").value) || 6));
  const evaluatorCount = Math.max(0, Math.min(6, Number($("setup-evaluator-count").value) || 0));
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(settingsPayload()) });
    const data = await api("/api/roles/generate", {
      method: "POST",
      body: JSON.stringify({ topic, count, evaluators: evaluatorCount, background: $("setup-background").value }),
    });
    const roles = data.roles || [];
    state.editorRoles = roles.filter((role) => !role.evaluator);
    state.editorEvaluators = roles.filter((role) => role.evaluator);
    renderEditor();
    showAlert(`已生成 ${state.editorRoles.length} 个讨论角色 + ${state.editorEvaluators.length} 个评估者`, "ok");
  } catch (error) {
    showAlert(`生成角色失败：${error.message}`);
  }
}

async function saveConfig() {
  const topic = $("setup-topic").value.trim();
  if (!topic) {
    showAlert("先填写讨论主题");
    return false;
  }
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(settingsPayload()) });
    await api("/api/config", { method: "POST", body: JSON.stringify({ topic, roles: collectRoles(), background: $("setup-background").value }) });
    showAlert("配置已保存", "ok");
    return true;
  } catch (error) {
    showAlert(`保存失败：${error.message}`);
    return false;
  }
}

async function startDiscussion() {
  if (!(await saveConfig())) return;
  const topic = $("setup-topic").value.trim();
  try {
    await api("/api/discussion/start", { method: "POST", body: JSON.stringify({ new: true, topic, roles: collectRoles(), background: $("setup-background").value }) });
    closeSetup();
    showAlert("讨论启动中，稍等角色输出", "ok");
    poll();
  } catch (error) {
    showAlert(`启动失败：${error.message}`);
  }
}

function bind() {
  $("new-btn").addEventListener("click", openSetup);
  $("setup-close").addEventListener("click", closeSetup);
  $("test-model-btn").addEventListener("click", testModel);
  $("generate-roles-btn").addEventListener("click", generateRoles);
  $("add-role-btn").addEventListener("click", () => {
    state.editorRoles.push({ name: "新角色", stance: "", goal: "", color: "#6ea8ff", evaluator: false });
    renderEditor();
  });
  $("add-evaluator-btn").addEventListener("click", () => {
    state.editorEvaluators.push({ name: "新评估者", stance: "", goal: "", color: "#f6e05e", evaluator: true });
    renderEditor();
  });
  $("save-config-btn").addEventListener("click", saveConfig);
  $("start-discussion-btn").addEventListener("click", startDiscussion);
  $("pause-btn").addEventListener("click", togglePause);
  $("stop-btn").addEventListener("click", stopDiscussion);
  $("archive-btn").addEventListener("click", archiveDiscussion);
  $("archive-close").addEventListener("click", closeArchive);
  $("export-btn").addEventListener("click", exportDiscussion);
  $("composer").addEventListener("submit", sendMessage);
  $("side-form").addEventListener("submit", sendSidechat);
  $("message").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      $("composer").requestSubmit();
    }
  });
  $("side-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      $("side-form").requestSubmit();
    }
  });
  const dropZone = $("drop-zone");
  dropZone.addEventListener("dragover", (event) => { event.preventDefault(); dropZone.classList.add("dragging"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
    if (event.dataTransfer?.files?.length) handleFiles(event.dataTransfer.files);
  });
  $("setup-modal").addEventListener("dragover", (event) => event.preventDefault());
  $("setup-modal").addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer?.files?.length) handleFiles(event.dataTransfer.files);
  });
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    $("setup-modal").classList.remove("hidden");
    handleFiles(event.dataTransfer.files);
  });
  $("background-file").addEventListener("change", async (event) => {
    const files = event.target.files;
    if (files && files.length) await handleFiles(files);
    event.target.value = "";
  });
  for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  for (const tab of document.querySelectorAll(".rtab")) tab.addEventListener("click", () => switchRightTab(tab.dataset.rtab));
  setInterval(poll, 1200);
}

async function init() {
  bind();
  await poll();
  await loadSidechat();
  await loadHistory();
  switchTab("feed");
  switchRightTab("pane");
}

init().catch((error) => showAlert(`初始化失败：${error.message}`));
