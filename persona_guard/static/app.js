(() => {
  "use strict";

  const API = {
    status: "/api/status",
    discoveries: "/api/discoveries",
    bindings: "/api/bindings",
    policy: "/api/policy",
    records: "/api/records"
  };

  const state = {
    status: null,
    statusError: "",
    discoveries: { threads: [], workspaces: [] },
    discoveriesError: "",
    bindings: [],
    bindingsError: "",
    policy: { text: "", revision: null },
    policyError: "",
    records: [],
    recordsError: "",
    recordFilters: { bindingId: "", result: "" },
    openRecordIds: new Set(),
    pendingBindingDeleteId: null,
    policyConfirmationOpen: false,
    recordConfirmation: null,
    editingBindingId: "",
    globalMutationPending: false,
    lastUpdated: null,
    refreshPromise: null,
    toastTimer: null
  };

  const $ = (id) => document.getElementById(id);

  function firstDefined(...values) {
    return values.find((value) => value !== undefined && value !== null);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[character]));
  }

  function asObject(payload) {
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      if (payload.data && typeof payload.data === "object" && !Array.isArray(payload.data)) {
        return payload.data;
      }
      return payload;
    }
    return {};
  }

  function collection(payload, key) {
    if (Array.isArray(payload)) return payload;
    const source = asObject(payload);
    return Array.isArray(source[key]) ? source[key] : [];
  }

  function apiErrorMessage(payload, fallback) {
    return payload && payload.error && payload.error.message
      ? String(payload.error.message)
      : fallback;
  }

  async function request(path, options = {}) {
    const headers = { Accept: "application/json" };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";

    let response;
    try {
      response = await fetch(path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
    } catch (error) {
      throw new Error("无法连接本地服务。");
    }

    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch (error) {
        payload = null;
      }
    }

    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `请求失败（${response.status}）。`));
    }
    return payload;
  }

  function jsonBody(value) {
    return JSON.stringify(value);
  }

  function displayValue(value, fallback = "—") {
    if (value === undefined || value === null || value === "") return fallback;
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function jsonText(value, fallback = "API 未提供") {
    if (value === undefined || value === null) return fallback;
    if (typeof value === "string") return value || fallback;
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

  function booleanValue(value, fallback = false) {
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value !== 0;
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (["true", "yes", "on", "enabled", "healthy", "ok", "up", "running"].includes(normalized)) return true;
      if (["false", "no", "off", "disabled", "unhealthy", "down"].includes(normalized)) return false;
    }
    return fallback;
  }

  function formatDate(value) {
    if (value === undefined || value === null || value === "") return "时间未知";
    let date;
    if (typeof value === "number") {
      date = new Date(value < 100000000000 ? value * 1000 : value);
    } else {
      date = new Date(value);
    }
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  function dateSortValue(value) {
    if (typeof value === "number") return value < 100000000000 ? value * 1000 : value;
    const parsed = Date.parse(value || "");
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function bindingId(binding) {
    return firstDefined(binding && binding.id, binding && binding.binding_id, "");
  }

  function recordBindingSnapshot(record) {
    return firstDefined(record.binding_snapshot, record.binding, record.matched_binding, null);
  }

  function recordBindingId(record) {
    const snapshot = recordBindingSnapshot(record);
    return firstDefined(record.binding_id, record.matched_binding_id, snapshot && snapshot.id, snapshot && snapshot.binding_id, "");
  }

  function recordBindingName(record) {
    const snapshot = recordBindingSnapshot(record);
    const current = state.bindings.find((binding) => String(bindingId(binding)) === String(recordBindingId(record)));
    return firstDefined(
      snapshot && snapshot.name,
      record.binding_name,
      current && current.name,
      recordBindingId(record) ? "已删除的绑定" : "绑定不可用"
    );
  }

  function recordId(record, index) {
    return String(firstDefined(record.id, record.record_id, `${record.created_at || record.timestamp || "record"}-${index}`));
  }

  function recordResult(record) {
    const raw = firstDefined(record.result, record.decision, record.outcome, record.error_category ? "ERROR" : "NONE");
    return String(raw || "NONE").toUpperCase();
  }

  function recordTimestamp(record) {
    return firstDefined(record.created_at, record.recorded_at, record.timestamp, record.created, record.last_seen, "");
  }

  function recordHistory(record) {
    return firstDefined(record.detector_history, record.history, record.transcript_history, record.input_history, record.transcript, record.messages);
  }

  function recordPrompt(record) {
    return firstDefined(record.current_prompt, record.user_prompt, record.prompt, record.latest_prompt);
  }

  function recordPolicy(record) {
    const snapshot = firstDefined(record.policy_snapshot, record.policy, null);
    if (snapshot !== null) return snapshot;
    const text = firstDefined(record.policy_text, record.detector_policy);
    const revision = firstDefined(record.policy_revision, record.revision);
    if (text !== undefined || revision !== undefined) return { text, revision };
    return undefined;
  }

  function recordStateBefore(record) {
    return firstDefined(record.state_before, record.guard_state_before, record.before_state);
  }

  function recordStateAfter(record) {
    return firstDefined(record.state_after, record.guard_state_after, record.after_state);
  }

  function recordError(record) {
    return firstDefined(record.error_category, record.error, record.failure_category);
  }

  function recordInjection(record) {
    return firstDefined(record.injected, record.injection, record.injection_outcome);
  }

  function setText(id, value) {
    const element = $(id);
    if (element) element.textContent = value;
  }

  function setFeedback(id, message, type = "") {
    const element = $(id);
    if (!element) return;
    element.textContent = message || "";
    element.classList.toggle("feedback-error", type === "error");
    element.classList.toggle("feedback-success", type === "success");
  }

  function notify(message, type = "success") {
    const toast = $("toast");
    if (!toast) return;
    window.clearTimeout(state.toastTimer);
    toast.textContent = message;
    toast.className = `toast ${type === "error" ? "error" : ""}`.trim();
    toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 4200);
  }

  function statusObject(payload) {
    const source = asObject(payload);
    return source.status && typeof source.status === "object" ? source.status : source;
  }

  function renderStatus() {
    const status = state.status;
    const globalSwitch = $("global-enabled");
    const connection = $("connection-status");

    if (!status) {
      setText("service-health", state.statusError ? "不可用" : "加载中…");
      setText("key-status", "—");
      setText("model-status", "—");
      setText("policy-revision-status", "—");
      setText("binding-count-status", "—");
      setText("record-count-status", "—");
      setText("runtime-state-badge", "未知");
      $("runtime-state-badge")?.classList.add("status-neutral");
      $("runtime-state-badge")?.classList.remove("status-on", "status-off");
      if (globalSwitch && !state.globalMutationPending) globalSwitch.disabled = true;
      if (connection) {
        connection.classList.toggle("is-online", false);
        connection.classList.toggle("is-offline", Boolean(state.statusError));
      }
      setText("connection-label", state.statusError ? "服务不可用" : "正在连接…");
      return;
    }

    const enabledValue = firstDefined(status.enabled, status.global_enabled, status.guard_enabled);
    const keyPresent = firstDefined(status.key_present, status.deepseek_key_present, status.has_api_key, status.api_key_present);
    const healthValue = firstDefined(status.service_health, status.health, status.service_status, status.healthy, status.ok);
    const model = firstDefined(status.active_model, status.detector_model, status.model, "—");
    const policyRevision = firstDefined(status.policy_revision, status.revision, state.policy.revision, "—");
    const bindingCount = firstDefined(status.binding_count, status.bindings_count, state.bindings.length, "—");
    const recordCount = firstDefined(status.record_count, status.records_count, state.records.length, "—");
    const enabled = booleanValue(enabledValue, false);
    const healthy = healthValue === undefined ? true : booleanValue(healthValue, false);

    if (globalSwitch && !state.globalMutationPending) {
      globalSwitch.checked = enabled;
      globalSwitch.setAttribute("aria-checked", String(enabled));
      globalSwitch.disabled = false;
    }
    setText("service-health", healthValue === undefined ? "正常" : (healthy ? "正常" : displayValue(healthValue)));
    setText("key-status", keyPresent === undefined ? "未知" : (booleanValue(keyPresent) ? "已检测到" : "缺失"));
    setText("model-status", displayValue(model));
    setText("policy-revision-status", displayValue(policyRevision));
    setText("binding-count-status", displayValue(bindingCount));
    setText("record-count-status", displayValue(recordCount));
    setText("runtime-state-badge", enabled ? "已启用" : "已关闭");
    const runtimeBadge = $("runtime-state-badge");
    runtimeBadge?.classList.toggle("status-on", enabled);
    runtimeBadge?.classList.toggle("status-off", !enabled);
    runtimeBadge?.classList.remove("status-neutral");

    if (connection) {
      connection.classList.toggle("is-online", healthy && !state.statusError);
      connection.classList.toggle("is-offline", !healthy || Boolean(state.statusError));
    }
    setText("connection-label", healthy && !state.statusError ? "服务在线" : "服务不可用");
  }

  function updateLastUpdated() {
    const time = state.lastUpdated ? state.lastUpdated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
    const connectionLabel = $("connection-label");
    if (connectionLabel && state.status && !state.statusError) {
      const statusText = connectionLabel.textContent.split(" · ")[0];
      connectionLabel.textContent = `${statusText} · ${time}`;
    }
  }

  function targetRecords(type) {
    if (type === "workspace") {
      return state.discoveries.workspaces.map((item) => {
        if (typeof item === "string") return { value: item, label: item, detail: "精确工作区" };
        const value = firstDefined(item.cwd, item.target_value, item.workspace, item.value, item.id, "");
        return { value: String(value), label: String(value), detail: "精确工作区" };
      }).filter((item) => item.value);
    }

    return state.discoveries.threads.map((item) => {
      if (typeof item === "string") return { value: item, label: item, detail: "Codex 线程" };
      const value = firstDefined(item.session_id, item.target_value, item.thread_id, item.id, "");
      const cwd = firstDefined(item.cwd, item.workspace, "");
      return { value: String(value), label: String(value), detail: cwd ? `工作区：${cwd}` : "Codex 线程" };
    }).filter((item) => item.value);
  }

  function renderTargetOptions(preservedValue) {
    const select = $("binding-target-value");
    const typeSelect = $("binding-target-type");
    if (!select || !typeSelect) return;
    const currentValue = preservedValue === undefined ? select.value : String(preservedValue || "");
    const type = typeSelect.value || "thread";
    const targets = targetRecords(type);
    const options = ['<option value="">选择已发现目标…</option>'];

    targets.forEach((target) => {
      options.push(`<option value="${escapeHtml(target.value)}">${escapeHtml(target.label)}</option>`);
    });

    if (currentValue && !targets.some((target) => target.value === currentValue)) {
      options.push(`<option value="${escapeHtml(currentValue)}">${escapeHtml(currentValue)}（当前值）</option>`);
    }
    select.innerHTML = options.join("");
    select.value = currentValue;
    if (select.value !== currentValue) select.value = "";
  }

  function renderDiscoveries() {
    const container = $("discoveries-list");
    if (!container) return;
    if (state.discoveriesError) {
      container.innerHTML = `<p class="empty-message">目标加载失败：${escapeHtml(state.discoveriesError)}</p>`;
      renderTargetOptions();
      return;
    }

    const threads = targetRecords("thread");
    const workspaces = targetRecords("workspace");
    if (!threads.length && !workspaces.length) {
      container.innerHTML = '<p class="empty-message">暂未发现目标。目标线程发送下一条消息后会出现在这里。</p>';
      renderTargetOptions();
      return;
    }

    const threadItems = threads.map((target) => {
      const original = state.discoveries.threads.find((item) => String(firstDefined(item && item.session_id, item && item.target_value, item && item.thread_id, item && item.id, item)) === target.value);
      const lastSeen = original && typeof original === "object" ? firstDefined(original.last_seen, original.updated_at, "") : "";
      return `<div class="discovery-item">
        <div class="discovery-copy">
          <strong>Codex 线程</strong>
          <code>${escapeHtml(target.value)}</code>
          <span>${escapeHtml(target.detail)}${lastSeen ? ` · 最近出现 ${escapeHtml(formatDate(lastSeen))}` : ""}</span>
        </div>
        <button class="button" type="button" data-action="use-target" data-target-type="thread" data-target-value="${escapeHtml(target.value)}">使用目标</button>
      </div>`;
    }).join("");
    const workspaceItems = workspaces.map((target) => {
      const original = state.discoveries.workspaces.find((item) => String(firstDefined(item && item.cwd, item && item.target_value, item && item.workspace, item && item.value, item && item.id, item)) === target.value);
      const lastSeen = original && typeof original === "object" ? firstDefined(original.last_seen, original.updated_at, "") : "";
      return `<div class="discovery-item">
        <div class="discovery-copy">
          <strong>精确工作区</strong>
          <code>${escapeHtml(target.value)}</code>
          <span>${lastSeen ? `最近出现 ${escapeHtml(formatDate(lastSeen))}` : "仅精确匹配"}</span>
        </div>
        <button class="button" type="button" data-action="use-target" data-target-type="workspace" data-target-value="${escapeHtml(target.value)}">使用目标</button>
      </div>`;
    }).join("");

    const groups = [];
    if (threadItems) groups.push(`<div class="discovery-group"><div class="discovery-group-heading"><span>线程</span><span>${threads.length}</span></div>${threadItems}</div>`);
    if (workspaceItems) groups.push(`<div class="discovery-group"><div class="discovery-group-heading"><span>工作区</span><span>${workspaces.length}</span></div>${workspaceItems}</div>`);
    container.innerHTML = groups.join("");
    renderTargetOptions();
  }

  function bindingPayload(binding, overrides = {}) {
    return {
      name: firstDefined(overrides.name, binding.name, ""),
      target_type: firstDefined(overrides.target_type, binding.target_type, "thread"),
      target_value: firstDefined(overrides.target_value, binding.target_value, ""),
      enabled: firstDefined(overrides.enabled, binding.enabled, true),
      reminder: firstDefined(overrides.reminder, binding.reminder, "")
    };
  }

  function renderBindingCard(binding) {
    const id = String(bindingId(binding));
    const enabled = booleanValue(binding.enabled, true);
    const typeLabel = binding.target_type === "workspace" ? "精确工作区" : "Codex 线程";
    const pending = state.pendingBindingDeleteId === id;
    return `<article class="binding-card ${enabled ? "" : "is-disabled"}">
      <div class="binding-card-header">
        <div class="binding-card-heading">
          <span class="target-kind ${binding.target_type === "workspace" ? "result-watch" : "result-hit"}">${escapeHtml(typeLabel)}</span>
          <h4>${escapeHtml(displayValue(binding.name, "未命名绑定"))}</h4>
          <p>${enabled ? "此绑定正在生效。" : "绑定已关闭；状态与记录仍会保留。"}</p>
        </div>
        <label class="binding-switch" for="binding-enabled-${escapeHtml(id)}">
          <input id="binding-enabled-${escapeHtml(id)}" type="checkbox" role="switch" aria-checked="${String(enabled)}" data-action="toggle-binding" data-binding-id="${escapeHtml(id)}" ${enabled ? "checked" : ""} />
          <span>启用</span>
        </label>
      </div>
      <div class="binding-details">
        <div class="binding-detail">
          <span class="binding-detail-label">目标</span>
          <code class="binding-target">${escapeHtml(displayValue(binding.target_value))}</code>
        </div>
        <div class="binding-detail">
          <span class="binding-detail-label">HIT Reminder</span>
          <p>${escapeHtml(displayValue(binding.reminder))}</p>
        </div>
      </div>
      <div class="card-actions">
        <button class="button" type="button" data-action="edit-binding" data-binding-id="${escapeHtml(id)}">编辑</button>
        <button class="button danger" type="button" data-action="delete-binding" data-binding-id="${escapeHtml(id)}">删除绑定</button>
      </div>
      ${pending ? `<div class="inline-confirm" role="alert">
        <p><strong>删除这个绑定？</strong> 对应 Guard State 会被移除，校准记录仍会保留。</p>
        <div class="confirm-actions">
          <button class="button" type="button" data-action="cancel-delete-binding">取消</button>
          <button class="button danger" type="button" data-action="confirm-delete-binding" data-binding-id="${escapeHtml(id)}">确认删除</button>
        </div>
      </div>` : ""}
    </article>`;
  }

  function renderBindings() {
    const list = $("binding-list");
    if (!list) return;
    setText("binding-count-label", `${state.bindings.length} 个绑定`);
    if (state.bindingsError) {
      list.innerHTML = `<p class="empty-message">绑定加载失败：${escapeHtml(state.bindingsError)}</p>`;
      renderRecordFilters();
      return;
    }
    if (!state.bindings.length) {
      list.innerHTML = '<p class="empty-message">还没有绑定。请从上方选择一个已发现目标。</p>';
      renderRecordFilters();
      return;
    }
    list.innerHTML = state.bindings.map(renderBindingCard).join("");
    renderRecordFilters();
  }

  function resetBindingForm() {
    const form = $("binding-form");
    if (!form) return;
    state.editingBindingId = "";
    form.reset();
    $("binding-id").value = "";
    $("binding-target-type").value = "thread";
    $("binding-enabled").checked = true;
    setText("binding-form-heading", "创建绑定");
    setText("binding-submit", "创建绑定");
    $("binding-cancel").hidden = true;
    setFeedback("binding-form-feedback", "");
    renderTargetOptions();
  }

  function editBinding(id) {
    const binding = state.bindings.find((item) => String(bindingId(item)) === String(id));
    if (!binding) return;
    state.editingBindingId = String(id);
    $("binding-id").value = String(id);
    $("binding-name").value = displayValue(binding.name, "");
    $("binding-target-type").value = binding.target_type === "workspace" ? "workspace" : "thread";
    const targetValue = displayValue(binding.target_value, "");
    renderTargetOptions(targetValue);
    $("binding-target-value").value = targetValue;
    $("binding-reminder").value = displayValue(binding.reminder, "");
    $("binding-enabled").checked = booleanValue(binding.enabled, true);
    setText("binding-form-heading", "编辑绑定");
    setText("binding-submit", "保存绑定");
    $("binding-cancel").hidden = false;
    setFeedback("binding-form-feedback", "正在编辑此绑定。");
    $("binding-editor-panel")?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }

  function recordBindingOptions() {
    const options = new Map();
    state.bindings.forEach((binding) => {
      const id = bindingId(binding);
      if (id !== "") options.set(String(id), { name: displayValue(binding.name, "未命名绑定"), deleted: false });
    });
    state.records.forEach((record) => {
      const id = recordBindingId(record);
      if (id === "" || options.has(String(id))) return;
      const snapshot = recordBindingSnapshot(record);
      options.set(String(id), { name: firstDefined(snapshot && snapshot.name, record.binding_name, "已删除绑定"), deleted: true });
    });
    return options;
  }

  function renderRecordFilters() {
    const select = $("record-binding-filter");
    if (!select) return;
    const options = ['<option value="">全部绑定</option>'];
    recordBindingOptions().forEach((item, id) => {
      options.push(`<option value="${escapeHtml(id)}">${escapeHtml(item.name)}${item.deleted ? "（已删除）" : ""}</option>`);
    });
    if (state.recordFilters.bindingId && !recordBindingOptions().has(String(state.recordFilters.bindingId))) {
      options.push(`<option value="${escapeHtml(state.recordFilters.bindingId)}">已选绑定</option>`);
    }
    select.innerHTML = options.join("");
    select.value = state.recordFilters.bindingId;
    if (select.value !== state.recordFilters.bindingId) {
      state.recordFilters.bindingId = "";
      select.value = "";
    }
    const resultSelect = $("record-result-filter");
    if (resultSelect) resultSelect.value = state.recordFilters.result;
    const clearBindingButton = $("clear-binding-records");
    if (clearBindingButton) clearBindingButton.disabled = !state.recordFilters.bindingId;
  }

  function captureRecordExpansion() {
    const list = $("records-list");
    if (!list) return;
    list.querySelectorAll("details.record-card[data-record-id]").forEach((detail) => {
      const id = detail.getAttribute("data-record-id");
      if (!id) return;
      if (detail.open) state.openRecordIds.add(id);
      else state.openRecordIds.delete(id);
    });
  }

  function renderRecord(record, index) {
    const id = recordId(record, index);
    const result = recordResult(record);
    const safeResult = ["HIT", "WATCH", "NONE", "ERROR"].includes(result) ? result : "NONE";
    const timestamp = recordTimestamp(record);
    const bindingIdValue = recordBindingId(record);
    const snapshot = recordBindingSnapshot(record);
    const policy = recordPolicy(record);
    const rawRecord = jsonText(record, "未提供");
    return `<details class="record-card" data-record-id="${escapeHtml(id)}" ${state.openRecordIds.has(id) ? "open" : ""}>
      <summary>
        <span class="result-badge result-${safeResult.toLowerCase()}">${safeResult}</span>
        <span class="record-summary-copy">
          <strong class="record-binding-name">${escapeHtml(recordBindingName(record))}</strong>
          <span class="record-meta">${escapeHtml(formatDate(timestamp))}${record.latency_ms !== undefined || record.latency !== undefined ? ` · ${escapeHtml(displayValue(firstDefined(record.latency_ms, record.latency)))} ms` : ""}</span>
        </span>
      </summary>
      <div class="record-details">
        <div class="record-overview">
          <div class="record-overview-item"><span>记录 ID</span><strong class="mono">${escapeHtml(id)}</strong></div>
          <div class="record-overview-item"><span>绑定 ID</span><strong class="mono">${escapeHtml(displayValue(bindingIdValue))}</strong></div>
          <div class="record-overview-item"><span>注入内容</span><strong>${escapeHtml(displayValue(recordInjection(record)))}</strong></div>
          <div class="record-overview-item"><span>模型</span><strong class="wrap-anywhere">${escapeHtml(displayValue(firstDefined(record.model, record.detector_model)))}</strong></div>
        </div>
        <div class="record-detail-grid">
          <section class="record-detail-block wide">
            <h4>送入检测器的原始历史</h4>
            <pre>${escapeHtml(jsonText(recordHistory(record)))}</pre>
          </section>
          <section class="record-detail-block wide">
            <h4>当前用户提示词</h4>
            <pre>${escapeHtml(jsonText(recordPrompt(record)))}</pre>
          </section>
          <section class="record-detail-block">
            <h4>检测提示词快照</h4>
            <pre>${escapeHtml(jsonText(policy))}</pre>
          </section>
          <section class="record-detail-block">
            <h4>绑定快照</h4>
            <pre>${escapeHtml(jsonText(snapshot || (bindingIdValue ? { binding_id: bindingIdValue } : undefined)))}</pre>
          </section>
          <section class="record-detail-block">
            <h4>判断前状态</h4>
            <pre>${escapeHtml(jsonText(recordStateBefore(record)))}</pre>
          </section>
          <section class="record-detail-block">
            <h4>判断后状态</h4>
            <pre>${escapeHtml(jsonText(recordStateAfter(record)))}</pre>
          </section>
          <section class="record-detail-block">
            <h4>判断 / 错误</h4>
            <pre>${escapeHtml(jsonText({ result: recordResult(record), type: firstDefined(record.type, record.decision_type), error_category: recordError(record) }))}</pre>
          </section>
          <section class="record-detail-block">
            <h4>时间信息</h4>
            <pre>${escapeHtml(jsonText({ created_at: timestamp, latency_ms: firstDefined(record.latency_ms, record.latency), model: firstDefined(record.model, record.detector_model) }))}</pre>
          </section>
        </div>
        <details class="raw-record">
          <summary>原始记录 JSON</summary>
          <pre>${escapeHtml(rawRecord)}</pre>
        </details>
      </div>
    </details>`;
  }

  function renderRecords() {
    const list = $("records-list");
    if (!list) return;
    captureRecordExpansion();
    setText("record-count-label", `${state.records.length} 条记录`);
    setFeedback("records-feedback", state.recordsError ? `记录加载失败：${state.recordsError}` : "", state.recordsError ? "error" : "");
    if (!state.records.length) {
      list.innerHTML = '<p class="empty-message">没有符合当前筛选条件的校准记录。</p>';
      return;
    }
    list.innerHTML = state.records.map(renderRecord).join("");
  }

  function renderPolicy() {
    const textArea = $("policy-text");
    if (textArea && !textArea.matches(":focus") && !textArea.dataset.dirty) {
      textArea.value = state.policy.text || "";
    }
    const revision = firstDefined(state.policy.revision, state.status && state.status.policy_revision, "—");
    setText("policy-revision-label", `版本 ${displayValue(revision)}`);
    setFeedback("policy-feedback", state.policyError ? `检测提示词加载失败：${state.policyError}` : "", state.policyError ? "error" : "");
    $("policy-confirmation").hidden = !state.policyConfirmationOpen;
  }

  async function loadStatus() {
    try {
      const payload = await request(API.status);
      state.status = statusObject(payload);
      state.statusError = "";
    } catch (error) {
      state.statusError = error.message;
    }
    renderStatus();
  }

  async function loadDiscoveries() {
    try {
      const payload = await request(API.discoveries);
      const source = asObject(payload);
      state.discoveries = {
        threads: collection(source, "threads"),
        workspaces: collection(source, "workspaces")
      };
      state.discoveriesError = "";
    } catch (error) {
      state.discoveriesError = error.message;
    }
    renderDiscoveries();
  }

  async function loadBindings() {
    try {
      const payload = await request(API.bindings);
      state.bindings = collection(payload, "bindings");
      state.bindingsError = "";
    } catch (error) {
      state.bindingsError = error.message;
    }
    renderBindings();
  }

  async function loadPolicy() {
    try {
      const payload = await request(API.policy);
      const source = asObject(payload);
      state.policy = {
        text: firstDefined(source.text, source.policy_text, ""),
        revision: firstDefined(source.revision, source.policy_revision, null)
      };
      state.policyError = "";
    } catch (error) {
      state.policyError = error.message;
    }
    renderPolicy();
  }

  async function loadRecords() {
    try {
      const params = new URLSearchParams();
      if (state.recordFilters.bindingId) params.set("binding_id", state.recordFilters.bindingId);
      if (state.recordFilters.result) params.set("result", state.recordFilters.result);
      params.set("limit", "50");
      const payload = await request(`${API.records}?${params.toString()}`);
      state.records = collection(payload, "records");
      state.records.sort((a, b) => {
        const aId = Number(a.id);
        const bId = Number(b.id);
        if (Number.isFinite(aId) && Number.isFinite(bId) && aId !== bId) return bId - aId;
        return dateSortValue(recordTimestamp(b)) - dateSortValue(recordTimestamp(a));
      });
      state.recordsError = "";
    } catch (error) {
      state.recordsError = error.message;
    }
    renderRecordFilters();
    renderRecords();
  }

  async function refreshDashboard(includePolicy = false) {
    if (state.refreshPromise) return state.refreshPromise;
    state.refreshPromise = (async () => {
      const jobs = [loadStatus(), loadDiscoveries(), loadBindings(), loadRecords()];
      if (includePolicy) jobs.push(loadPolicy());
      await Promise.all(jobs);
      state.lastUpdated = new Date();
      updateLastUpdated();
    })().finally(() => {
      state.refreshPromise = null;
    });
    return state.refreshPromise;
  }

  async function setGlobalEnabled(enabled, input) {
    state.globalMutationPending = true;
    if (input) input.disabled = true;
    setFeedback("global-switch-feedback", enabled ? "正在启用门卫…" : "正在关闭门卫…");
    try {
      await request(API.status, { method: "PUT", body: jsonBody({ enabled }) });
      notify(enabled ? "全局门卫已启用。" : "全局门卫已关闭。");
      await refreshDashboard();
      setFeedback("global-switch-feedback", enabled ? "门卫已启用。" : "门卫已关闭；已有状态保持不变。", "success");
    } catch (error) {
      if (input) {
        input.checked = !enabled;
        input.setAttribute("aria-checked", String(!enabled));
      }
      setFeedback("global-switch-feedback", error.message, "error");
      notify(error.message, "error");
      renderStatus();
    } finally {
      state.globalMutationPending = false;
      renderStatus();
    }
  }

  async function saveBinding(form) {
    const id = $("binding-id").value;
    const payload = {
      name: $("binding-name").value.trim(),
      target_type: $("binding-target-type").value,
      target_value: $("binding-target-value").value,
      enabled: $("binding-enabled").checked,
      reminder: $("binding-reminder").value
    };
    const submit = $("binding-submit");
    submit.disabled = true;
    setFeedback("binding-form-feedback", id ? "正在保存绑定…" : "正在创建绑定…");
    try {
      const path = id ? `${API.bindings}/${encodeURIComponent(id)}` : API.bindings;
      await request(path, { method: id ? "PUT" : "POST", body: jsonBody(payload) });
      notify(id ? "绑定已保存。" : "绑定已创建。");
      resetBindingForm();
      await refreshDashboard();
    } catch (error) {
      setFeedback("binding-form-feedback", error.message, "error");
      notify(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  }

  async function toggleBinding(input) {
    const id = input.getAttribute("data-binding-id");
    const binding = state.bindings.find((item) => String(bindingId(item)) === String(id));
    if (!binding) return;
    const enabled = input.checked;
    input.disabled = true;
    input.setAttribute("aria-checked", String(enabled));
    try {
      await request(`${API.bindings}/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: jsonBody(bindingPayload(binding, { enabled }))
      });
      notify(enabled ? "绑定已启用。" : "绑定已关闭。");
      await refreshDashboard();
    } catch (error) {
      input.checked = !enabled;
      input.setAttribute("aria-checked", String(!enabled));
      notify(error.message, "error");
      renderBindings();
    }
  }

  async function deleteBinding(id) {
    try {
      await request(`${API.bindings}/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (state.editingBindingId === String(id)) resetBindingForm();
      state.pendingBindingDeleteId = null;
      notify("绑定已删除，校准记录已保留。");
      await refreshDashboard();
    } catch (error) {
      notify(error.message, "error");
      setFeedback("bindings-feedback", error.message, "error");
    }
  }

  async function savePolicy() {
    const text = $("policy-text").value;
    if (!text.trim()) {
      setFeedback("policy-feedback", "检测提示词不能为空。", "error");
      return;
    }
    let feedbackMessage = "";
    let feedbackType = "";
    const submit = $("policy-submit");
    submit.disabled = true;
    setFeedback("policy-feedback", "正在保存检测提示词…");
    try {
      const payload = await request(API.policy, { method: "PUT", body: jsonBody({ text }) });
      const source = asObject(payload);
      state.policy = {
        text: firstDefined(source.text, text),
        revision: firstDefined(source.revision, source.policy_revision, state.policy.revision)
      };
      $("policy-text").dataset.dirty = "";
      state.policyConfirmationOpen = false;
      renderPolicy();
      notify("检测提示词已保存，所有门卫状态已重置为 NORMAL。");
      await refreshDashboard();
      feedbackMessage = "检测提示词已生效，所有门卫状态均为 NORMAL。";
      feedbackType = "success";
    } catch (error) {
      feedbackMessage = error.message;
      feedbackType = "error";
      notify(error.message, "error");
    } finally {
      submit.disabled = false;
      state.policyConfirmationOpen = false;
      renderPolicy();
      setFeedback("policy-feedback", feedbackMessage, feedbackType);
    }
  }

  function renderRecordConfirmation() {
    const container = $("records-confirmation");
    if (!container) return;
    const confirmation = state.recordConfirmation;
    if (!confirmation) {
      container.hidden = true;
      container.innerHTML = "";
      return;
    }
    const options = recordBindingOptions();
    const name = confirmation.bindingId && options.get(String(confirmation.bindingId))
      ? options.get(String(confirmation.bindingId)).name
      : "此绑定";
    const isAll = confirmation.kind === "all";
    container.innerHTML = `<p><strong>${isAll ? "清空全部校准记录？" : `清空 ${escapeHtml(name)} 的记录？`}</strong> 此操作无法在面板中撤销。</p>
      <div class="confirm-actions">
        <button class="button" type="button" data-action="cancel-record-clear">取消</button>
        <button class="button danger" type="button" data-action="confirm-record-clear">${isAll ? "清空全部记录" : "清空此绑定记录"}</button>
      </div>`;
    container.hidden = false;
  }

  async function clearRecords() {
    const confirmation = state.recordConfirmation;
    if (!confirmation) return;
    try {
      const path = confirmation.kind === "all"
        ? API.records
        : `${API.records}?binding_id=${encodeURIComponent(confirmation.bindingId)}`;
      await request(path, { method: "DELETE" });
      state.recordConfirmation = null;
      renderRecordConfirmation();
      notify(confirmation.kind === "all" ? "全部校准记录已清空。" : "此绑定的校准记录已清空。");
      await refreshDashboard();
    } catch (error) {
      notify(error.message, "error");
    }
  }

  function useTarget(type, value) {
    $("binding-target-type").value = type;
    renderTargetOptions("");
    $("binding-target-value").value = value;
    $("binding-editor-panel")?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    $("binding-name")?.focus();
  }

  function handleAction(actionElement) {
    const action = actionElement.getAttribute("data-action");
    if (action === "use-target") {
      useTarget(actionElement.getAttribute("data-target-type"), actionElement.getAttribute("data-target-value"));
    } else if (action === "edit-binding") {
      editBinding(actionElement.getAttribute("data-binding-id"));
    } else if (action === "delete-binding") {
      state.pendingBindingDeleteId = actionElement.getAttribute("data-binding-id");
      renderBindings();
    } else if (action === "cancel-delete-binding") {
      state.pendingBindingDeleteId = null;
      renderBindings();
    } else if (action === "confirm-delete-binding") {
      state.pendingBindingDeleteId = null;
      deleteBinding(actionElement.getAttribute("data-binding-id"));
    } else if (action === "cancel-policy-save") {
      state.policyConfirmationOpen = false;
      renderPolicy();
    } else if (action === "confirm-policy-save") {
      savePolicy();
    } else if (action === "cancel-record-clear") {
      state.recordConfirmation = null;
      renderRecordConfirmation();
    } else if (action === "confirm-record-clear") {
      clearRecords();
    }
  }

  function bindEvents() {
    $("global-enabled")?.addEventListener("change", (event) => {
      const input = event.currentTarget;
      input.setAttribute("aria-checked", String(input.checked));
      setGlobalEnabled(input.checked, input);
    });

    $("binding-target-type")?.addEventListener("change", () => renderTargetOptions());
    $("binding-cancel")?.addEventListener("click", resetBindingForm);
    $("binding-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      if (event.currentTarget.reportValidity()) saveBinding(event.currentTarget);
    });

    $("policy-text")?.addEventListener("input", (event) => {
      event.currentTarget.dataset.dirty = "true";
    });
    $("policy-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!event.currentTarget.reportValidity()) return;
      state.policyConfirmationOpen = true;
      renderPolicy();
    });

    $("record-binding-filter")?.addEventListener("change", (event) => {
      state.recordFilters.bindingId = event.currentTarget.value;
      state.recordConfirmation = null;
      renderRecordConfirmation();
      loadRecords();
    });
    $("record-result-filter")?.addEventListener("change", (event) => {
      state.recordFilters.result = event.currentTarget.value;
      loadRecords();
    });
    $("clear-all-records")?.addEventListener("click", () => {
      state.recordConfirmation = { kind: "all" };
      renderRecordConfirmation();
    });
    $("clear-binding-records")?.addEventListener("click", () => {
      if (!state.recordFilters.bindingId) return;
      state.recordConfirmation = { kind: "binding", bindingId: state.recordFilters.bindingId };
      renderRecordConfirmation();
    });
    $("refresh-button")?.addEventListener("click", async () => {
      await refreshDashboard(!$("policy-text").dataset.dirty);
      notify("面板已刷新。");
    });

    document.addEventListener("click", (event) => {
      const actionElement = event.target.closest("[data-action]");
      if (actionElement) handleAction(actionElement);
    });

    document.addEventListener("change", (event) => {
      const toggle = event.target.closest('input[data-action="toggle-binding"]');
      if (toggle) {
        toggle.setAttribute("aria-checked", String(toggle.checked));
        toggleBinding(toggle);
      }
    });

    $("records-list")?.addEventListener("toggle", (event) => {
      const detail = event.target.closest("details.record-card[data-record-id]");
      if (!detail) return;
      const id = detail.getAttribute("data-record-id");
      if (!id) return;
      if (detail.open) state.openRecordIds.add(id);
      else state.openRecordIds.delete(id);
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") refreshDashboard();
    });
  }

  function startPolling() {
    window.setInterval(() => {
      if (document.visibilityState === "visible") refreshDashboard();
    }, 2000);
  }

  async function init() {
    bindEvents();
    renderStatus();
    renderDiscoveries();
    renderBindings();
    renderPolicy();
    renderRecordFilters();
    renderRecords();
    await refreshDashboard(true);
    startPolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
