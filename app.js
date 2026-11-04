const byId = (id) => document.getElementById(id);
const refresh = byId("refresh");
const refreshDialog = byId("refresh-dialog");
const search = byId("search");
const records = byId("records");
const directoryStatus = byId("directory-status");
const form = byId("editor-form");
const feedback = byId("feedback");
const save = byId("save");
const discard = byId("discard");
const coreForm = byId("core-form");
const coreInput = byId("persona-core");
const coreFeedback = byId("core-feedback");
const coreSave = byId("core-save");
const coreDiscard = byId("core-discard");
const resetAll = byId("reset-all");
const resetDialog = byId("reset-dialog");
const resetCancel = byId("reset-cancel");
const resetConfirm = byId("reset-confirm");
const resetError = byId("reset-error");
const fields = {
  affection: byId("affection"),
  emotion: byId("emotion"),
  relationship: byId("relationship"),
  memory: byId("memory"),
  psychology: byId("psychology"),
  long_term_memory: byId("long-term-memory"),
  attitude: byId("attitude"),
};

let rows = [];
let selected = null;
let coreValue = null;
let emotionDefault = 70;
let bridge;
let loaded = false;
let busy = false;
const idleHint = "保存后，角色会在后续对话中使用这些值。";
const coreIdleHint = "保存后，所有会话都会使用新的人格核心。";

function rowKey(row) {
  return JSON.stringify([row.user_id, row.scope]);
}

function scopeLabel(scope) {
  const match = /^(.*?):(GroupMessage|FriendMessage):(.*)$/.exec(scope);
  if (!match) return scope;
  const [, platform, kind, id] = match;
  return kind === "GroupMessage" ? `${platform} · 群聊 ${id}` : `${platform} · 私聊 ${id}`;
}

function textElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function renderDirectory() {
  const query = search.value.trim().toLocaleLowerCase();
  const filtered = rows.filter((row) => [row.user_id, scopeLabel(row.scope), row.relationship]
    .join(" ").toLocaleLowerCase().includes(query));
  byId("record-count").textContent = String(rows.length);
  records.replaceChildren(...filtered.map((row) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    const id = row.user_id.split(":").at(-1);
    const label = row.relationship ? `${row.relationship} · ${id}` : id;
    const avatar = textElement("span", "avatar", id.slice(-2));
    avatar.setAttribute("aria-hidden", "true");
    const body = textElement("span", "record-text", "");
    body.append(
      textElement("span", "record-name", label),
      textElement("span", "record-scope", scopeLabel(row.scope)),
    );
    button.type = "button";
    button.className = "record";
    button.dataset.key = rowKey(row);
    button.setAttribute("aria-current", String(selected !== null && rowKey(row) === rowKey(selected)));
    button.disabled = busy;
    button.append(avatar, body);
    button.addEventListener("click", () => selectRow(row));
    item.append(button);
    return item;
  }));
  directoryStatus.dataset.tone = "";
  directoryStatus.textContent = rows.length
    ? (filtered.length ? `显示 ${filtered.length} 个会话` : "没有匹配的会话")
    : "还没有会话档案";
}

function setFeedback(message, tone = "", target = feedback) {
  target.textContent = message;
  target.dataset.tone = tone;
}

function readEmotion(value) {
  const number = typeof value === "string" && /^\d{1,3}$/.test(value.trim()) ? Number(value) : value;
  return Number.isInteger(number) && number >= 0 && number <= 100 ? number : null;
}

function textValue(value) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeRow(row) {
  const emotion = readEmotion(row.emotion);
  const affection = typeof row.affection === "string" && row.affection.trim() ? Number(row.affection) : row.affection;
  const memory = Array.isArray(row.memory) ? row.memory : textValue(row.memory).split(/\r?\n/);
  return {
    ...row,
    affection: row.creator ? 100 : Number.isFinite(affection) && affection >= 0 && affection <= 100 ? affection : 40,
    emotion: emotion ?? emotionDefault,
    emotionFallback: emotion === null,
    relationship: textValue(row.relationship),
    memory: memory.map(textValue).filter(Boolean),
    psychology: textValue(row.psychology),
    long_term_memory: textValue(row.long_term_memory),
    attitude: textValue(row.attitude),
  };
}

function coreChanged() {
  return coreValue !== null && coreInput.value.trim() !== coreValue;
}

function hasDraft() {
  return coreChanged() || Object.keys(changedFields()).length > 0;
}

function setCore(value) {
  coreValue = typeof value === "string" ? value.trim() : null;
  coreInput.value = coreValue ?? "";
  const hint = coreValue === null ? "未读取到人格核心，请刷新后重试。" : coreIdleHint;
  setFeedback(hint, "", coreFeedback);
}

function changedFields() {
  if (!selected) return {};
  const current = {
    affection: fields.affection.valueAsNumber,
    emotion: fields.emotion.valueAsNumber,
    relationship: fields.relationship.value.trim(),
    memory: fields.memory.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
    psychology: fields.psychology.value.trim(),
    long_term_memory: fields.long_term_memory.value.trim(),
    attitude: fields.attitude.value.trim(),
  };
  if (selected.creator) delete current.affection;
  return Object.fromEntries(Object.entries(current).filter(([key, value]) =>
    Array.isArray(value) ? JSON.stringify(value) !== JSON.stringify(selected[key]) : value !== selected[key],
  ));
}

function updateControls() {
  const changes = changedFields();
  const dirty = Object.keys(changes).length > 0;
  const coreDirty = coreChanged();
  refresh.disabled = busy;
  search.disabled = busy || !loaded;
  byId("fields").disabled = busy;
  save.disabled = busy || !dirty;
  discard.disabled = busy || !dirty;
  coreInput.disabled = busy || coreValue === null;
  coreSave.disabled = busy || !coreDirty;
  coreDiscard.disabled = busy || !coreDirty;
  resetAll.disabled = busy || !loaded;
  resetCancel.disabled = busy;
  resetConfirm.disabled = busy;
  for (const button of records.querySelectorAll("button")) button.disabled = busy;
  for (const key of ["affection", "emotion"]) {
    byId(`${key}-meter`).value = Number.isFinite(fields[key].valueAsNumber) ? fields[key].valueAsNumber : 0;
  }
  return dirty;
}

function fillEditor(row) {
  selected = row;
  form.hidden = !row;
  byId("empty").hidden = Boolean(row);
  if (!row) {
    form.reset();
    byId("empty-title").textContent = "暂无角色状态";
    byId("empty-description").textContent = "和角色聊几句，再回来刷新。";
    return;
  }

  byId("profile-title").textContent = row.user_id;
  byId("profile-scope").textContent = scopeLabel(row.scope);
  byId("creator-badge").hidden = !row.creator;
  fields.affection.disabled = Boolean(row.creator);
  byId("affection-note").textContent = row.creator ? "创建者的好感度固定为 100" : "同一用户的各会话共用";
  fields.affection.value = row.affection.toFixed(2);
  fields.emotion.value = String(row.emotion);
  byId("emotion-note").textContent = row.emotionFallback ? "暂无记录，显示默认参考值" : "角色在当前会话的情绪分值";
  fields.relationship.value = row.relationship;
  fields.memory.value = row.memory.join("\n");
  fields.psychology.value = row.psychology;
  fields.long_term_memory.value = row.long_term_memory;
  fields.attitude.value = row.attitude;
  for (const button of records.querySelectorAll("button")) {
    button.setAttribute("aria-current", String(button.dataset.key === rowKey(row)));
  }
  setFeedback(idleHint);
  updateControls();
}

function selectRow(row) {
  if (busy || (selected && rowKey(row) === rowKey(selected))) return;
  if (Object.keys(changedFields()).length) {
    setFeedback("还有未保存的修改。请先保存或取消。", "error");
    return;
  }
  fillEditor(row);
  if (window.matchMedia("(max-width: 710px)").matches) form.scrollIntoView({ block: "start" });
}

function replaceStatus(status) {
  if (!Array.isArray(status?.users)) throw new Error("会话档案格式有误。");
  const currentKey = selected && rowKey(selected);
  emotionDefault = readEmotion(status.emotion_default) ?? emotionDefault;
  rows = status.users.filter((row) => textValue(row?.user_id) && textValue(row?.scope)).map(normalizeRow);
  loaded = true;
  setCore(status.persona_core);
  fillEditor(rows.find((row) => rowKey(row) === currentKey) || rows[0] || null);
  renderDirectory();
}

async function withReadTimeout(promise, milliseconds, message) {
  let timer;
  try {
    const timeout = new Promise((_, reject) => {
      timer = window.setTimeout(() => reject(new Error(message)), milliseconds);
    });
    return await Promise.race([promise, timeout]);
  } finally {
    window.clearTimeout(timer);
  }
}

async function loadStatus() {
  if (busy) return;
  busy = true;
  updateControls();
  directoryStatus.dataset.tone = "";
  directoryStatus.textContent = "正在读取会话档案…";
  try {
    const candidate = window.AstrBotPluginView;
    if (!candidate) throw new Error("请从 AstrBot 插件详情打开此视图。");
    if (candidate !== bridge) {
      await withReadTimeout(candidate.ready(), 4000, "连接 AstrBot 超时。");
      bridge = candidate;
    }
    const result = await withReadTimeout(bridge.apiGet("status"), 12000, "读取超时。");
    replaceStatus(result);
  } catch (error) {
    directoryStatus.dataset.tone = "error";
    directoryStatus.textContent = `读取失败，请重试。${error.message || ""}`;
    if (!selected) {
      byId("empty-title").textContent = "未能读取角色状态";
      byId("empty-description").textContent = "请点击刷新重试。";
    }
    if (coreValue === null) setFeedback("未读取到人格核心，请刷新后重试。", "error", coreFeedback);
  } finally {
    busy = false;
    updateControls();
  }
}

form.addEventListener("input", () => {
  setFeedback(updateControls() ? "有未保存的修改" : idleHint);
});
discard.addEventListener("click", () => fillEditor(selected));
search.addEventListener("input", renderDirectory);
refresh.addEventListener("click", () => {
  if (hasDraft()) refreshDialog.showModal();
  else loadStatus();
});
byId("refresh-cancel").addEventListener("click", () => refreshDialog.close());
byId("refresh-confirm").addEventListener("click", () => {
  refreshDialog.close();
  loadStatus();
});

coreInput.addEventListener("input", () => {
  setFeedback(coreChanged() ? "有未保存的人格核心修改" : coreIdleHint, "", coreFeedback);
  updateControls();
});

coreDiscard.addEventListener("click", () => {
  setCore(coreValue);
  updateControls();
});

coreForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !coreChanged() || !coreForm.reportValidity()) return;
  busy = true;
  setFeedback("正在保存…", "", coreFeedback);
  updateControls();
  try {
    const result = await bridge.apiPost("persona-core", { persona_core: coreInput.value.trim() });
    if (typeof result?.persona_core !== "string") throw new Error("人格核心返回格式有误。");
    setCore(result.persona_core);
    setFeedback("人格核心已保存", "saved", coreFeedback);
  } catch (error) {
    setFeedback(`保存失败，修改已保留。${error.message || "请重试。"}`, "error", coreFeedback);
  } finally {
    busy = false;
    updateControls();
  }
});

resetAll.addEventListener("click", () => {
  byId("reset-draft").hidden = !hasDraft();
  resetError.hidden = true;
  resetDialog.showModal();
});
resetCancel.addEventListener("click", () => resetDialog.close());
resetDialog.addEventListener("cancel", (event) => {
  if (busy) event.preventDefault();
});

resetConfirm.addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  resetError.hidden = true;
  resetConfirm.textContent = "正在重置…";
  updateControls();
  try {
    const result = await bridge.apiPost("reset", { confirm: true });
    replaceStatus(result);
    const notice = byId("reset-feedback");
    notice.textContent = "已恢复默认状态";
    notice.dataset.tone = "saved";
    resetDialog.close();
  } catch (error) {
    resetError.textContent = `无法确认重置结果。${error.message || "请重试。"}`;
    resetError.hidden = false;
  } finally {
    busy = false;
    resetConfirm.textContent = "确认重置";
    updateControls();
    if (!resetDialog.open) resetAll.focus();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !selected || !form.reportValidity()) return;
  const changes = changedFields();
  if (!Object.keys(changes).length) return;
  if (changes.memory && (changes.memory.length > 20 || changes.memory.some((line) => [...line].length > 240))) {
    setFeedback("记忆最多 20 条，每条不能超过 240 个字。", "error");
    fields.memory.focus();
    return;
  }

  busy = true;
  setFeedback("正在保存…");
  updateControls();
  try {
    const { user_id, scope } = selected;
    const result = await bridge.apiPost("status", { user_id, scope, ...changes });
    const user = normalizeRow(result.user);
    const key = rowKey(user);
    rows = rows.map((row) => {
      if (rowKey(row) === key) return user;
      if (row.user_id !== user.user_id) return row;
      // Scores and relationships are shared by the same user across scopes.
      return { ...row, affection: user.affection, relationship: user.relationship };
    });
    fillEditor(user);
    renderDirectory();
    setFeedback("已保存", "saved");
  } catch (error) {
    setFeedback(`保存失败，修改已保留。${error.message || "请重试。"}`, "error");
  } finally {
    busy = false;
    updateControls();
  }
});

await loadStatus();
