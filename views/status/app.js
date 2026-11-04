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
const creatorIdentity = byId("identity-creator");
const identityInputs = [...document.querySelectorAll('input[name="identity"]')];
const initializationNotice = byId("initialization-notice");
const stateUpdateNote = byId("state-update-note");
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
let creatorPreview = false;
let ordinaryAffectionDraft = "";
let coreValue = null;
let coreTarget = null;
let fallbackCore = "";
let emotionDefault = 70;
let bridge;
let loaded = false;
let busy = false;
let initializingKey = null;
let polling = false;
const initializationFailures = new Set();
const idleHint = "保存后，角色会在后续对话中使用这些值。";
const coreIdleHint = "保存后，该人物会使用新的人格核心。";

function rowKey(row) {
  return JSON.stringify([row.user_id, row.scope, row.persona_key ?? ""]);
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
  const filtered = rows.filter((row) => [row.user_id, row.persona_name, scopeLabel(row.scope), row.relationship]
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
      textElement("span", "record-scope", [row.persona_name, scopeLabel(row.scope)].filter(Boolean).join(" · ")),
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

function readAffection(value) {
  const number = typeof value === "string" && value.trim() ? Number(value) : value;
  return Number.isFinite(number) && number >= 0 && number <= 100 ? number : null;
}

function normalizeRow(row) {
  const emotion = readEmotion(row.emotion);
  const affection = readAffection(row.affection);
  const memory = Array.isArray(row.memory) ? row.memory : textValue(row.memory).split(/\r?\n/);
  const identities = Array.isArray(row.identities) ? row.identities : [];
  const creator = typeof row.creator === "boolean" ? row.creator : identities.includes("creator");
  const currentIdentities = identityInputs.filter(({ value }) =>
    value === "creator" ? creator : identities.includes(value),
  );
  return {
    ...row,
    creator,
    affection: creator ? 100 : affection ?? 40,
    base_affection: readAffection(row.base_affection) ?? (creator ? null : affection ?? 40),
    emotion: emotion ?? emotionDefault,
    emotionFallback: emotion === null,
    relationship: textValue(row.relationship),
    memory: memory.map(textValue).filter(Boolean),
    psychology: textValue(row.psychology),
    long_term_memory: textValue(row.long_term_memory),
    attitude: textValue(row.attitude),
    identities: currentIdentities.map(({ value }) => value),
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

function setCoreFromRow(row) {
  coreTarget = row;
  coreInput.maxLength = row?.persona_core_source === "astrbot" ? 32000 : 4000;
  setCore(typeof row?.persona_core === "string" ? row.persona_core : fallbackCore);
  let description = "当前使用插件默认的人格核心。";
  if (row?.persona_core_source === "astrbot") {
    description = `${row.persona_name || "当前人物"}使用 AstrBot 人格提示词，保存后会直接更新该人格。`;
  } else if (row) {
    description = `${row.persona_name || "当前人物"}使用插件的人格核心。`;
  }
  byId("core-description").textContent = description;
}

function targetForRow(row) {
  const { user_id, scope, persona_key } = row;
  return persona_key ? { user_id, scope, persona_key } : { user_id, scope };
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
    identities: identityInputs.filter(({ checked }) => checked).map(({ value }) => value),
  };
  if (creatorIdentity.checked) delete current.affection;
  return Object.fromEntries(Object.entries(current).filter(([key, value]) =>
    Array.isArray(value) ? JSON.stringify(value) !== JSON.stringify(selected[key])
      : value !== (key === "affection" ? selected.base_affection ?? selected.affection : selected[key]),
  ));
}

function updateAffinityControl() {
  if (!selected) return;
  if (creatorPreview !== creatorIdentity.checked) {
    if (creatorIdentity.checked) ordinaryAffectionDraft = fields.affection.value;
    fields.affection.value = creatorIdentity.checked ? "100.00" : ordinaryAffectionDraft;
    creatorPreview = creatorIdentity.checked;
  }
  fields.affection.disabled = creatorIdentity.checked;
  byId("affection-note").textContent = creatorIdentity.checked
    ? "创建者的好感度固定为 100"
    : "同一用户的各会话共用";
}

function updateControls() {
  updateAffinityControl();
  const changes = changedFields();
  const dirty = Object.keys(changes).length > 0;
  const coreDirty = coreChanged();
  const sessionBusy = busy || initializingKey !== null;
  initializationNotice.hidden = initializingKey === null;
  form.setAttribute("aria-busy", String(initializingKey !== null));
  refresh.disabled = sessionBusy;
  search.disabled = sessionBusy || !loaded;
  byId("fields").disabled = sessionBusy;
  save.disabled = sessionBusy || !dirty;
  discard.disabled = sessionBusy || !dirty;
  coreInput.disabled = busy || coreValue === null;
  coreSave.disabled = sessionBusy || !coreDirty;
  coreDiscard.disabled = busy || !coreDirty;
  resetAll.disabled = sessionBusy || !loaded;
  resetCancel.disabled = busy;
  resetConfirm.disabled = busy;
  for (const button of records.querySelectorAll("button")) button.disabled = sessionBusy;
  for (const key of ["affection", "emotion"]) {
    byId(`${key}-meter`).value = Number.isFinite(fields[key].valueAsNumber) ? fields[key].valueAsNumber : 0;
  }
  return dirty;
}

function formatDelta(value, places) {
  const number = Number.isFinite(value) ? value : 0;
  return `${number > 0 ? "+" : ""}${number.toFixed(places)}`;
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
  byId("profile-scope").textContent = [row.persona_name, scopeLabel(row.scope)].filter(Boolean).join(" · ");
  byId("creator-badge").hidden = !row.creator;
  const update = row.state_update;
  stateUpdateNote.hidden = !update;
  stateUpdateNote.dataset.phase = update?.phase ?? "";
  stateUpdateNote.textContent = update?.phase === "pending"
    ? "正在后台整理这轮的心理活动和记忆，完成后会自动同步。你可以继续聊天或编辑。"
    : ["error", "skipped"].includes(update?.phase) ? update.message || "本轮档案整理未完成，原记忆已保留。"
      : update?.phase === "updated" ? update.message || `本轮档案已同步：好感度 ${formatDelta(update.affection_delta, 2)}，情绪 ${formatDelta(update.emotion_delta, 0)}。没有新事实时记忆保持不变。` : "";
  for (const input of identityInputs) input.checked = row.identities.includes(input.value);
  creatorPreview = creatorIdentity.checked;
  ordinaryAffectionDraft = (row.base_affection ?? row.affection).toFixed(2);
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
  if (busy || initializingKey !== null) return;
  if (selected && rowKey(row) === rowKey(selected)) {
    initializeSelected();
    return;
  }
  if (Object.keys(changedFields()).length) {
    setFeedback("还有未保存的修改。请先保存或取消。", "error");
    return;
  }
  if (coreChanged()) {
    setFeedback("人格核心还有未保存的修改，请先保存或取消。", "error", coreFeedback);
    return;
  }
  fillEditor(row);
  setCoreFromRow(row);
  if (window.matchMedia("(max-width: 710px)").matches) form.scrollIntoView({ block: "start" });
  initializeSelected();
}

function validateStatusVersion(status) {
  if (status?.schema_version !== 2) {
    throw new Error("状态接口版本不匹配，请重载插件并刷新页面；仍无效时重启 AstrBot。");
  }
}

function replaceRows(status, { preserveSessionDraft = false } = {}) {
  validateStatusVersion(status);
  if (!Array.isArray(status?.users)) throw new Error("会话档案格式有误。");
  const currentKey = selected && rowKey(selected);
  const keepCoreDraft = coreChanged();
  const keepSessionDraft = preserveSessionDraft && Object.keys(changedFields()).length > 0;
  if (typeof status.persona_core === "string") fallbackCore = status.persona_core;
  emotionDefault = readEmotion(status.emotion_default) ?? emotionDefault;
  rows = status.users.filter((row) => textValue(row?.user_id) && textValue(row?.scope)).map(normalizeRow);
  loaded = true;
  const currentRow = rows.find((row) => rowKey(row) === currentKey)
    || rows.find((row) => row.user_id === selected?.user_id && row.scope === selected?.scope)
    || rows[0] || null;
  if (!keepSessionDraft || !currentRow || rowKey(currentRow) !== currentKey) fillEditor(currentRow);
  renderDirectory();
  if (!keepCoreDraft) setCoreFromRow(currentRow);
}

function replaceStatus(status) {
  validateStatusVersion(status);
  if (!Array.isArray(status?.users)) throw new Error("会话档案格式有误。");
  coreValue = null;
  replaceRows(status);
}

async function initializeSelected() {
  if (!selected || selected.initialized !== false || busy || initializingKey !== null) return;
  const key = rowKey(selected);
  if (initializationFailures.has(key) || Object.keys(changedFields()).length) return;
  const target = targetForRow(selected);
  initializingKey = key;
  setFeedback("正在初始化角色状态…");
  updateControls();
  try {
    const result = await withReadTimeout(bridge.apiPost("initialize", target), 60000, "初始化超时。");
    replaceRows(result);
    setFeedback("角色状态已初始化", "saved");
  } catch (error) {
    initializationFailures.add(key);
    setFeedback(`初始化失败，可点击刷新重试。${error.message || ""}`, "error");
  } finally {
    initializingKey = null;
    updateControls();
  }
}

function syncUserRow(row, saved, changes) {
  if (rowKey(row) === rowKey(saved)) return saved;
  const sameUser = row.user_id === saved.user_id;
  const samePersona = (row.persona_key ?? "") === (saved.persona_key ?? "");
  let creator = row.creator;
  if (changes.identities && samePersona && (saved.creator || sameUser)) creator = saved.creator && sameUser;
  const identities = (sameUser ? saved.identities : row.identities).filter((identity) => identity !== "creator");
  if (creator) identities.unshift("creator");
  const baseAffection = sameUser ? saved.base_affection : row.base_affection;
  const affection = creator ? 100 : baseAffection ?? row.affection;
  return {
    ...row, creator, identities, affection, base_affection: baseAffection,
    relationship: sameUser ? saved.relationship : row.relationship,
  };
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
  if (busy || initializingKey !== null) return;
  busy = true;
  updateControls();
  directoryStatus.dataset.tone = "";
  directoryStatus.textContent = "正在读取会话档案…";
  let loadedSuccessfully = false;
  try {
    const candidate = window.AstrBotPluginView;
    if (!candidate) throw new Error("请从 AstrBot 插件详情打开此视图。");
    if (candidate !== bridge) {
      await withReadTimeout(candidate.ready(), 4000, "连接 AstrBot 超时。");
      bridge = candidate;
    }
    const result = await withReadTimeout(bridge.apiGet("status"), 12000, "读取超时。");
    replaceStatus(result);
    initializationFailures.clear();
    loadedSuccessfully = true;
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
  if (loadedSuccessfully) await initializeSelected();
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
  if (busy || initializingKey !== null || !coreChanged() || !coreForm.reportValidity()) return;
  busy = true;
  setFeedback("正在保存…", "", coreFeedback);
  updateControls();
  try {
    const target = coreTarget ? targetForRow(coreTarget) : {};
    const result = await bridge.apiPost("persona-core", { ...target, persona_core: coreInput.value.trim() });
    validateStatusVersion(result);
    if (Array.isArray(result.users)) {
      coreValue = null;
      replaceRows(result, { preserveSessionDraft: true });
    } else {
      if (typeof result?.persona_core !== "string") throw new Error("人格核心返回格式有误。");
      setCore(result.persona_core);
      if (coreTarget) {
        const persona = coreTarget.persona_key ?? "";
        rows = rows.map((row) => (row.persona_key ?? "") === persona ? { ...row, persona_core: result.persona_core } : row);
        coreTarget = rows.find((row) => rowKey(row) === rowKey(coreTarget)) || coreTarget;
        if (selected) selected = rows.find((row) => rowKey(row) === rowKey(selected)) || selected;
        renderDirectory();
      } else {
        fallbackCore = result.persona_core;
      }
    }
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
    const target = targetForRow(selected);
    const result = await bridge.apiPost("status", { ...target, ...changes });
    validateStatusVersion(result);
    if (Array.isArray(result.users)) {
      replaceRows(result);
    } else {
      const user = normalizeRow(result.user);
      rows = rows.map((row) => syncUserRow(row, user, changes));
      fillEditor(user);
      renderDirectory();
    }
    setFeedback("已保存", "saved");
    if (changes.identities && !Array.isArray(result.users)) {
      try {
        const status = await withReadTimeout(bridge.apiGet("status"), 12000, "读取超时。");
        replaceRows(status);
        setFeedback("已保存", "saved");
      } catch {
        setFeedback("已保存。刷新后可查看其他会话的最新身份。", "saved");
      }
    }
  } catch (error) {
    setFeedback(`保存失败，修改已保留。${error.message || "请重试。"}`, "error");
  } finally {
    busy = false;
    updateControls();
  }
});

await loadStatus();

// Read back completed background updates without replacing any unsaved input.
window.setInterval(async () => {
  if (polling || busy || initializingKey !== null || !loaded || !bridge || document.hidden || hasDraft()) return;
  polling = true;
  const key = selected && rowKey(selected);
  try {
    const result = await withReadTimeout(bridge.apiGet("status"), 12000, "自动同步超时。");
    if (!busy && initializingKey === null && !hasDraft() && key === (selected && rowKey(selected))) {
      replaceRows(result, { preserveSessionDraft: true });
      updateControls();
    }
  } catch {
    // Keep the editable data intact; explicit refresh still reports read failures.
  } finally {
    polling = false;
  }
}, 5000);
