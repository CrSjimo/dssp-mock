(() => {
  "use strict";

  const API = {
    config: "/api/config",
    status: "/api/status",
    logs: "/api/logs",
  };

  const state = {
    config: null,
    selectedInstance: 0,
    selectedArchitecture: 0,
    selectedSinger: 0,
    dirty: false,
    saving: false,
    loading: true,
    instanceStatus: new Map(),
    resourceStatus: {},
    logs: [],
    seenLogs: new Set(),
    logCursor: null,
    logsPaused: false,
    statusTimer: null,
    logTimer: null,
    controlBusy: false,
  };

  const dom = {};

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function toNumber(value) {
    return value === "" ? null : Number(value);
  }

  function splitLanguages(value) {
    return value
      .split(/[,，\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function uniqueId(prefix, values) {
    const used = new Set(values);
    let number = 1;
    while (used.has(`${prefix}-${number}`)) number += 1;
    return `${prefix}-${number}`;
  }

  function normalizeConfig(payload) {
    const source = payload && payload.config ? payload.config : payload;
    const config = source && typeof source === "object" ? structuredClone(source) : {};
    config.resource_host ??= "127.0.0.1";
    config.resource_port ??= 7861;
    config.resource_public_base_url ??= null;
    config.instances = Array.isArray(config.instances) ? config.instances : [];

    config.instances.forEach((instance) => {
      instance.id ??= "instance";
      instance.name ??= instance.id;
      instance.host ??= "127.0.0.1";
      instance.port ??= 13711;
      instance.autostart ??= false;
      instance.parameter_sample_rate ??= 100;
      instance.media_mode ??= "data_url";
      instance.resource_ttl_seconds ??= 300;
      instance.architectures = Array.isArray(instance.architectures)
        ? instance.architectures
        : [];

      instance.architectures.forEach((arch) => {
        arch.id ??= "architecture";
        arch.name ??= arch.id;
        arch.pronunciation_mode ??= "FULL";
        arch.phoneme_mode ??= "FULL";
        arch.parameters = Array.isArray(arch.parameters) ? arch.parameters : [];
        arch.audio_dependencies = Array.isArray(arch.audio_dependencies)
          ? arch.audio_dependencies
          : [];
        arch.singers = Array.isArray(arch.singers) ? arch.singers : [];

        arch.parameters.forEach((parameter) => {
          parameter.name ??= "parameter";
          parameter.type ??= "INDIRECT";
          parameter.depends_on = Array.isArray(parameter.depends_on)
            ? parameter.depends_on
            : [];
          if (parameter.type === "DIRECT") parameter.depends_on = [];
          if (parameter.name === "pitch") {
            parameter.min_value = 0;
            parameter.max_value = 12800;
          } else {
            parameter.min_value ??= -1000;
            parameter.max_value ??= 1000;
          }
        });

        arch.singers.forEach((singer) => {
          singer.id ??= "singer";
          singer.name ??= singer.id;
          singer.mix_group ??= "default";
          singer.languages = Array.isArray(singer.languages) ? singer.languages : [];
          singer.default_language ??= singer.languages[0] ?? "";
          singer.mock_key ??= singer.id;
          singer.demo_audios = Array.isArray(singer.demo_audios)
            ? singer.demo_audios
            : [];
          singer.demo_audios = singer.demo_audios.map((item) => ({ name: item?.name ?? "" }));
        });
      });
    });
    return config;
  }

  function newInstance() {
    const instances = state.config?.instances ?? [];
    const id = uniqueId("instance", instances.map((item) => item.id));
    const usedPorts = new Set(instances.map((item) => Number(item.port)));
    let port = 13711;
    while (usedPorts.has(port) || port === Number(state.config?.resource_port)) port += 1;
    return {
      id,
      name: `Mock 实例 ${instances.length + 1}`,
      host: "127.0.0.1",
      port,
      autostart: false,
      parameter_sample_rate: 100,
      media_mode: "data_url",
      resource_ttl_seconds: 300,
      architectures: [],
    };
  }

  function newArchitecture(instance) {
    const id = uniqueId("arch", instance.architectures.map((item) => item.id));
    return {
      id,
      name: `合成架构 ${instance.architectures.length + 1}`,
      pronunciation_mode: "FULL",
      phoneme_mode: "FULL",
      parameters: [],
      audio_dependencies: [],
      singers: [],
    };
  }

  function newParameter(arch) {
    return {
      name: uniqueId("parameter", arch.parameters.map((item) => item.name)),
      type: "INDIRECT",
      depends_on: [],
      min_value: -1000,
      max_value: 1000,
    };
  }

  function newSinger(arch) {
    const id = uniqueId("singer", arch.singers.map((item) => item.id));
    const usedMockKeys = (currentInstance()?.architectures ?? [])
      .flatMap((item) => item.singers)
      .map((item) => item.mock_key);
    return {
      id,
      name: `歌手 ${arch.singers.length + 1}`,
      mix_group: "default",
      languages: ["zh"],
      default_language: "zh",
      mock_key: uniqueId("mock-key", usedMockKeys),
      demo_audios: [],
    };
  }

  function currentInstance() {
    return state.config?.instances?.[state.selectedInstance] ?? null;
  }

  function currentArchitecture() {
    return currentInstance()?.architectures?.[state.selectedArchitecture] ?? null;
  }

  function currentSinger() {
    return currentArchitecture()?.singers?.[state.selectedSinger] ?? null;
  }

  function clampSelections() {
    const instanceCount = state.config?.instances?.length ?? 0;
    state.selectedInstance = instanceCount
      ? Math.min(Math.max(state.selectedInstance, 0), instanceCount - 1)
      : 0;
    const archCount = currentInstance()?.architectures?.length ?? 0;
    state.selectedArchitecture = archCount
      ? Math.min(Math.max(state.selectedArchitecture, 0), archCount - 1)
      : 0;
    const singerCount = currentArchitecture()?.singers?.length ?? 0;
    state.selectedSinger = singerCount
      ? Math.min(Math.max(state.selectedSinger, 0), singerCount - 1)
      : 0;
  }

  function markDirty() {
    if (!state.config || state.loading) return;
    state.dirty = true;
    dom.saveButton.disabled = state.saving;
    setSaveState("有未保存的更改", "warning");
  }

  function setSaveState(message, tone = "neutral") {
    dom.saveState.textContent = message;
    dom.saveState.dataset.tone = tone;
  }

  function statusLabel(status) {
    const normalized = String(status ?? "unknown").toLowerCase();
    const labels = {
      running: "运行中",
      started: "运行中",
      starting: "启动中",
      stopped: "已停止",
      stopping: "停止中",
      restarting: "重启中",
      error: "异常",
      failed: "异常",
      unknown: "未知",
    };
    return labels[normalized] ?? String(status);
  }

  function statusTone(status) {
    const normalized = String(status ?? "unknown").toLowerCase();
    if (["running", "started"].includes(normalized)) return "running";
    if (["starting", "stopping", "restarting"].includes(normalized)) return "pending";
    if (["error", "failed"].includes(normalized)) return "error";
    if (normalized === "stopped") return "stopped";
    return "unknown";
  }

  function normalizeStatus(payload) {
    const result = new Map();
    const resource = payload?.resource && typeof payload.resource === "object"
      ? payload.resource
      : {};
    let entries = payload?.instances ?? payload?.statuses ?? payload;
    if (Array.isArray(entries)) {
      entries.forEach((item) => {
        const id = item?.id ?? item?.instance_id;
        if (id != null) result.set(String(id), item);
      });
    } else if (entries && typeof entries === "object") {
      Object.entries(entries).forEach(([id, item]) => {
        if (id !== "resource" && item && typeof item === "object") result.set(id, { id, ...item });
      });
    }
    return { instances: result, resource };
  }

  function renderAll() {
    clampSelections();
    renderResourceFields();
    renderInstanceList();
    renderWorkspace();
    renderLogFilter();
    renderLogs();
    renderStatuses();
  }

  function field(label, input, help = "", className = "") {
    return `
      <label class="form-field ${className}">
        <span class="field-label">${label}</span>
        ${input}
        ${help ? `<span class="field-help">${help}</span>` : ""}
      </label>`;
  }

  function renderResourceFields() {
    if (!state.config) {
      dom.resourceFields.innerHTML = '<div class="skeleton wide"></div>';
      return;
    }
    const config = state.config;
    dom.resourceFields.innerHTML = [
      field(
        "监听 IP / 主机",
        `<input data-resource-field="resource_host" type="text" value="${escapeHtml(config.resource_host)}" spellcheck="false" />`,
      ),
      field(
        "监听端口",
        `<input data-resource-field="resource_port" type="number" min="1" max="65535" step="1" value="${escapeHtml(config.resource_port)}" />`,
      ),
      field(
        "公开基础 URL（可选）",
        `<input data-resource-field="resource_public_base_url" type="url" placeholder="http://127.0.0.1:7861" value="${escapeHtml(config.resource_public_base_url ?? "")}" spellcheck="false" />`,
        "留空时根据监听地址生成 URL。",
        "span-2",
      ),
    ].join("");
  }

  function renderInstanceList() {
    if (!state.config) {
      dom.instanceList.innerHTML = '<div class="skeleton-list"><i></i><i></i><i></i></div>';
      return;
    }
    if (!state.config.instances.length) {
      dom.instanceList.innerHTML = `
        <div class="sidebar-empty">
          <strong>还没有实例</strong>
          <span>点击右上角 + 创建第一个。</span>
        </div>`;
      return;
    }
    dom.instanceList.innerHTML = state.config.instances
      .map((instance, index) => {
        const info = state.instanceStatus.get(instance.id) ?? {};
        const rawStatus = info.status ?? (info.running === true ? "running" : info.running === false ? "stopped" : "unknown");
        return `
          <button class="selection-item ${index === state.selectedInstance ? "selected" : ""}" data-select-instance="${index}" type="button" role="option" aria-selected="${index === state.selectedInstance}">
            <span class="selection-main">
              <strong>${escapeHtml(instance.name || instance.id || "未命名实例")}</strong>
              <small>${escapeHtml(instance.host)}:${escapeHtml(instance.port)}</small>
            </span>
            <span class="status-dot ${statusTone(rawStatus)}" data-status-id="${escapeHtml(instance.id)}" title="${escapeHtml(statusLabel(rawStatus))}"></span>
          </button>`;
      })
      .join("");
  }

  function renderWorkspace() {
    if (!state.config) {
      dom.instanceWorkspace.innerHTML = '<section class="card workspace-skeleton"><div class="skeleton wide"></div><div class="skeleton"></div><div class="skeleton tall"></div></section>';
      return;
    }
    const instance = currentInstance();
    if (!instance) {
      dom.instanceWorkspace.innerHTML = `
        <section class="card empty-state large">
          <div class="empty-icon">◇</div>
          <h2>创建一个 Mock 实例</h2>
          <p>每个实例拥有独立的 arch、歌手与运行端口。</p>
          <button class="button primary" data-action="add-instance" type="button">新增实例</button>
        </section>`;
      return;
    }

    const statusInfo = state.instanceStatus.get(instance.id) ?? {};
    const rawStatus = statusInfo.status ?? (statusInfo.running === true ? "running" : statusInfo.running === false ? "stopped" : "unknown");
    const issues = validateInstance(instance, state.selectedInstance);
    dom.instanceWorkspace.innerHTML = `
      <section class="card instance-card">
        <div class="instance-header">
          <div>
            <div class="title-line">
              <span id="instanceStatusBadge" class="status-badge ${statusTone(rawStatus)}">
                <i></i>${escapeHtml(statusLabel(rawStatus))}
              </span>
              <span class="instance-address">http://${escapeHtml(instance.host)}:${escapeHtml(instance.port)}/v1</span>
            </div>
            <h2>${escapeHtml(instance.name || "未命名实例")}</h2>
            <p id="instanceStatusDetails">${escapeHtml(statusInfo.error ?? statusInfo.message ?? "配置与运行状态相互独立；更改后请先保存。")}</p>
          </div>
          <div class="instance-actions">
            <button class="button success small" data-instance-action="start" type="button">启动</button>
            <button class="button secondary small" data-instance-action="stop" type="button">停止</button>
            <button class="button secondary small" data-instance-action="restart" type="button">重启</button>
            <button class="button danger-ghost small" data-action="delete-instance" type="button">删除</button>
          </div>
        </div>
        ${renderValidationSummary(issues)}
        <div class="form-grid instance-fields">
          ${field("实例 ID", `<input data-instance-field="id" type="text" value="${escapeHtml(instance.id)}" spellcheck="false" />`, "用于配置与日志标识，不可包含斜杠。")}
          ${field("显示名称", `<input data-instance-field="name" type="text" value="${escapeHtml(instance.name)}" />`)}
          ${field("监听 IP / 主机", `<input data-instance-field="host" type="text" value="${escapeHtml(instance.host)}" spellcheck="false" />`)}
          ${field("端口", `<input data-instance-field="port" type="number" min="1" max="65535" step="1" value="${escapeHtml(instance.port)}" />`)}
          ${field("参数响应采样率", `<div class="input-suffix"><input data-instance-field="parameter_sample_rate" type="number" min="0.01" max="10000" step="0.01" value="${escapeHtml(instance.parameter_sample_rate)}" /><span>Hz</span></div>`, "默认 100 Hz。")}
          ${field("媒体响应模式", `<select data-instance-field="media_mode"><option value="data_url" ${instance.media_mode === "data_url" ? "selected" : ""}>Data URL（内联）</option><option value="http" ${instance.media_mode === "http" ? "selected" : ""}>HTTP URL（临时资源）</option></select>`)}
          ${field("资源生存时间", `<div class="input-suffix"><input data-instance-field="resource_ttl_seconds" type="number" min="1" max="86400" step="1" value="${escapeHtml(instance.resource_ttl_seconds)}" ${instance.media_mode !== "http" ? "disabled" : ""} /><span>秒</span></div>`, "HTTP 模式有效，默认 300 秒。")}
          <label class="toggle-field">
            <span><strong>程序启动时自动运行</strong><small>加载持久化配置后自动启动此实例。</small></span>
            <input data-instance-field="autostart" type="checkbox" ${instance.autostart ? "checked" : ""} />
            <i aria-hidden="true"></i>
          </label>
        </div>
      </section>
      ${renderArchitectureSection(instance)}
    `;
  }

  function renderValidationSummary(issues) {
    if (!issues.length) return '<div class="validation-banner valid"><span>✓</span><p>当前实例结构通过前端校验</p></div>';
    return `
      <details class="validation-banner invalid">
        <summary><span>!</span><p>发现 ${issues.length} 个配置问题，保存前需要修正</p></summary>
        <ul>${issues.slice(0, 8).map((issue) => `<li>${escapeHtml(issue)}</li>`).join("")}${issues.length > 8 ? `<li>还有 ${issues.length - 8} 项…</li>` : ""}</ul>
      </details>`;
  }

  function renderArchitectureSection(instance) {
    const arch = currentArchitecture();
    return `
      <section class="card architecture-card">
        <div class="section-heading">
          <div>
            <span class="eyebrow">元数据</span>
            <h2>合成架构 Arch</h2>
          </div>
          <button class="button secondary small" data-action="add-architecture" type="button">+ 新增 Arch</button>
        </div>
        <div class="subnav" role="tablist" aria-label="Arch 列表">
          ${instance.architectures.length
            ? instance.architectures.map((item, index) => `<button class="subnav-item ${index === state.selectedArchitecture ? "selected" : ""}" data-select-architecture="${index}" type="button" role="tab" aria-selected="${index === state.selectedArchitecture}"><strong>${escapeHtml(item.name || item.id)}</strong><small>${escapeHtml(item.id)}</small></button>`).join("")
            : '<span class="subnav-empty">暂无 Arch</span>'}
        </div>
        ${arch ? renderArchitectureEditor(arch) : `
          <div class="empty-state inline">
            <div class="empty-icon small">⬡</div>
            <h3>为此实例添加 Arch</h3>
            <p>Arch 定义发音、音素模式、参数 DAG 和歌手。</p>
            <button class="button primary small" data-action="add-architecture" type="button">新增 Arch</button>
          </div>`}
      </section>`;
  }

  function renderArchitectureEditor(arch) {
    return `
      <div class="architecture-editor">
        <div class="editor-heading">
          <h3>Arch 基本信息</h3>
          <button class="button danger-ghost small" data-action="delete-architecture" type="button">删除此 Arch</button>
        </div>
        <div class="form-grid arch-fields">
          ${field("Arch ID", `<input data-arch-field="id" type="text" value="${escapeHtml(arch.id)}" spellcheck="false" />`)}
          ${field("显示名称", `<input data-arch-field="name" type="text" value="${escapeHtml(arch.name)}" />`)}
          ${field("发音模式", `<select data-arch-field="pronunciation_mode"><option value="FULL" ${arch.pronunciation_mode === "FULL" ? "selected" : ""}>FULL（提供发音生成）</option><option value="SKIP" ${arch.pronunciation_mode === "SKIP" ? "selected" : ""}>SKIP（不提供）</option></select>`)}
          ${field("音素模式", `<select data-arch-field="phoneme_mode"><option value="FULL" ${arch.phoneme_mode === "FULL" ? "selected" : ""}>FULL（音素与时长）</option><option value="TOKEN_ONLY" ${arch.phoneme_mode === "TOKEN_ONLY" ? "selected" : ""}>TOKEN_ONLY（仅音素）</option><option value="SKIP" ${arch.phoneme_mode === "SKIP" ? "selected" : ""}>SKIP（不提供）</option></select>`)}
        </div>
        ${renderParameterSection(arch)}
        ${renderSingerSection(arch)}
      </div>`;
  }

  function renderParameterSection(arch) {
    const graph = inspectGraph(arch);
    const names = arch.parameters.map((item) => item.name);
    return `
      <section class="nested-section parameter-section">
        <div class="nested-heading">
          <div>
            <h3>参数与依赖 DAG</h3>
            <p>DIRECT 只作为输入；INDIRECT 可被合成并可依赖其它参数。</p>
          </div>
          <button class="button secondary small" data-action="add-parameter" type="button">+ 新增参数</button>
        </div>
        ${graph.errors.length ? `<div class="graph-warning"><strong>DAG 无效</strong><span>${escapeHtml(graph.errors.join("；"))}</span></div>` : ""}
        <div class="parameter-layout">
          <div class="parameter-list">
            ${arch.parameters.length
              ? arch.parameters.map((parameter, index) => renderParameterRow(parameter, index, names, graph.cycleNames)).join("")
              : '<div class="mini-empty">暂无参数。可以从 pitch 或一个 DIRECT 控制量开始。</div>'}
          </div>
          <div class="dag-panel">
            <div class="dag-heading">
              <strong>依赖图</strong>
              <span><i class="legend direct"></i>DIRECT <i class="legend indirect"></i>INDIRECT <i class="legend audio"></i>音频依赖</span>
            </div>
            <div class="dag-canvas">${renderDagSvg(arch, graph)}</div>
          </div>
        </div>
        <div class="audio-dependencies">
          <div class="field-label">音频合成依赖
            <span class="field-help">选中的参数需随 audio 请求提供。</span>
          </div>
          <div class="check-grid">
            ${arch.parameters.length
              ? arch.parameters.map((parameter) => `<label class="check-chip"><input data-audio-dependency="${escapeHtml(parameter.name)}" type="checkbox" ${arch.audio_dependencies.includes(parameter.name) ? "checked" : ""} /><span>${escapeHtml(parameter.name || "（空名称）")}</span></label>`).join("")
              : '<span class="muted">添加参数后可选择。</span>'}
          </div>
        </div>
      </section>`;
  }

  function renderParameterRow(parameter, index, allNames, cycleNames) {
    const isDirect = parameter.type === "DIRECT";
    const isPitch = parameter.name === "pitch";
    const options = allNames.filter((_, itemIndex) => itemIndex !== index);
    return `
      <article class="parameter-row ${cycleNames.has(parameter.name) ? "has-cycle" : ""}">
        <div class="parameter-row-top">
          <span class="parameter-index">${index + 1}</span>
          <label class="compact-field grow">
            <span>参数名</span>
            <input data-parameter-index="${index}" data-parameter-field="name" data-original-name="${escapeHtml(parameter.name)}" type="text" value="${escapeHtml(parameter.name)}" spellcheck="false" />
          </label>
          <label class="compact-field type-field">
            <span>类型</span>
            <select data-parameter-index="${index}" data-parameter-field="type">
              <option value="INDIRECT" ${parameter.type === "INDIRECT" ? "selected" : ""}>INDIRECT</option>
              <option value="DIRECT" ${parameter.type === "DIRECT" ? "selected" : ""}>DIRECT</option>
            </select>
          </label>
          <button class="row-delete" data-action="delete-parameter" data-parameter-index="${index}" type="button" title="删除参数" aria-label="删除参数">×</button>
        </div>
        <div class="parameter-range ${isPitch ? "pitch-range" : ""}">
          <span class="range-title">数值范围</span>
          <label class="compact-field">
            <span>最小值</span>
            <input data-parameter-index="${index}" data-parameter-field="min_value" type="number" step="any" value="${escapeHtml(parameter.min_value)}" ${isPitch ? "readonly disabled" : ""} />
          </label>
          <span class="range-separator">至</span>
          <label class="compact-field">
            <span>最大值</span>
            <input data-parameter-index="${index}" data-parameter-field="max_value" type="number" step="any" value="${escapeHtml(parameter.max_value)}" ${isPitch ? "readonly disabled" : ""} />
          </label>
          <span class="range-help">${isPitch ? "pitch 范围固定为 0–12800，不可修改。" : "合成响应会限制在此范围内。"}</span>
        </div>
        <div class="dependency-picker ${isDirect ? "disabled" : ""}">
          <span>依赖参数</span>
          <div class="check-grid compact">
            ${isDirect
              ? '<span class="muted">DIRECT 参数不能声明依赖。</span>'
              : options.length
                ? options.map((name) => `<label class="check-chip small"><input data-parameter-index="${index}" data-parameter-dependency="${escapeHtml(name)}" type="checkbox" ${parameter.depends_on.includes(name) ? "checked" : ""} /><span>${escapeHtml(name || "（空名称）")}</span></label>`).join("")
                : '<span class="muted">暂无其它参数。</span>'}
          </div>
        </div>
      </article>`;
  }

  function syncParameterRangeControls(nameInput, parameter) {
    const range = nameInput.closest(".parameter-row")?.querySelector(".parameter-range");
    if (!range) return;
    const isPitch = parameter.name === "pitch";
    range.classList.toggle("pitch-range", isPitch);
    ["min_value", "max_value"].forEach((fieldName) => {
      const input = range.querySelector(`[data-parameter-field="${fieldName}"]`);
      if (!input) return;
      input.value = parameter[fieldName];
      input.disabled = isPitch;
      input.readOnly = isPitch;
    });
    const help = range.querySelector(".range-help");
    if (help) {
      help.textContent = isPitch
        ? "pitch 范围固定为 0–12800，不可修改。"
        : "合成响应会限制在此范围内。";
    }
  }

  function inspectGraph(arch) {
    const errors = [];
    const cycleNames = new Set();
    const nameCounts = new Map();
    arch.parameters.forEach((item) => nameCounts.set(item.name, (nameCounts.get(item.name) ?? 0) + 1));
    nameCounts.forEach((count, name) => {
      if (!name.trim()) errors.push("参数名不能为空");
      if (/[\\/]/.test(name)) errors.push(`参数名“${name}”不能包含斜杠`);
      if (count > 1) errors.push(`参数名“${name || "（空）"}”重复`);
    });
    const uniqueNames = new Set(arch.parameters.map((item) => item.name));
    arch.parameters.forEach((parameter) => {
      if (parameter.type === "DIRECT" && parameter.depends_on.length) {
        errors.push(`DIRECT 参数“${parameter.name}”不能有依赖`);
      }
      if (new Set(parameter.depends_on).size !== parameter.depends_on.length) {
        errors.push(`“${parameter.name}”的依赖参数不能重复`);
      }
      parameter.depends_on.forEach((dependency) => {
        if (!uniqueNames.has(dependency)) errors.push(`“${parameter.name}”依赖不存在的“${dependency}”`);
        if (dependency === parameter.name) errors.push(`“${parameter.name}”不能依赖自身`);
      });
    });

    if (![...nameCounts.values()].some((count) => count > 1)) {
      const graph = new Map(arch.parameters.map((item) => [item.name, item.depends_on.filter((name) => uniqueNames.has(name))]));
      const color = new Map();
      const stack = [];
      function visit(name) {
        if (color.get(name) === 2) return;
        if (color.get(name) === 1) {
          const start = stack.indexOf(name);
          stack.slice(start).forEach((item) => cycleNames.add(item));
          cycleNames.add(name);
          return;
        }
        color.set(name, 1);
        stack.push(name);
        (graph.get(name) ?? []).forEach(visit);
        stack.pop();
        color.set(name, 2);
      }
      graph.forEach((_, name) => visit(name));
      if (cycleNames.size) errors.push(`存在循环依赖：${[...cycleNames].join(" → ")}`);
    }
    return { errors: [...new Set(errors)], cycleNames };
  }

  function renderDagSvg(arch, graphInfo) {
    if (!arch.parameters.length) {
      return '<div class="dag-empty"><span>→</span><p>参数依赖将在这里可视化</p></div>';
    }
    const names = arch.parameters.map((item, index) => item.name || `（未命名 ${index + 1}）`);
    const indexByName = new Map();
    arch.parameters.forEach((item, index) => {
      if (!indexByName.has(item.name)) indexByName.set(item.name, index);
    });
    const depths = Array(arch.parameters.length).fill(0);
    if (!graphInfo.cycleNames.size) {
      for (let pass = 0; pass < arch.parameters.length; pass += 1) {
        arch.parameters.forEach((parameter, index) => {
          const dependencyDepths = parameter.depends_on
            .map((name) => indexByName.get(name))
            .filter((item) => item !== undefined)
            .map((item) => depths[item] + 1);
          if (dependencyDepths.length) depths[index] = Math.max(depths[index], ...dependencyDepths);
        });
      }
    } else {
      arch.parameters.forEach((_, index) => { depths[index] = index % 3; });
    }
    const groups = new Map();
    depths.forEach((depth, index) => {
      if (!groups.has(depth)) groups.set(depth, []);
      groups.get(depth).push(index);
    });
    const nodeWidth = 162;
    const nodeHeight = 58;
    const xGap = 218;
    const yGap = 88;
    const marginX = 36;
    const marginY = 34;
    const maxDepth = Math.max(...depths);
    const maxRows = Math.max(...[...groups.values()].map((items) => items.length));
    const width = Math.max(560, marginX * 2 + nodeWidth + maxDepth * xGap);
    const height = Math.max(170, marginY * 2 + nodeHeight + (maxRows - 1) * yGap);
    const positions = [];
    groups.forEach((indices, depth) => {
      const columnHeight = nodeHeight + (indices.length - 1) * yGap;
      const top = (height - columnHeight) / 2;
      indices.forEach((index, row) => {
        positions[index] = { x: marginX + depth * xGap, y: top + row * yGap };
      });
    });
    const edges = [];
    arch.parameters.forEach((parameter, targetIndex) => {
      parameter.depends_on.forEach((dependency) => {
        const sourceIndex = indexByName.get(dependency);
        if (sourceIndex === undefined || sourceIndex === targetIndex) return;
        const source = positions[sourceIndex];
        const target = positions[targetIndex];
        const x1 = source.x + nodeWidth;
        const y1 = source.y + nodeHeight / 2;
        const x2 = target.x;
        const y2 = target.y + nodeHeight / 2;
        const bend = Math.max(32, Math.abs(x2 - x1) * 0.45);
        const cyclic = graphInfo.cycleNames.has(parameter.name) && graphInfo.cycleNames.has(dependency);
        edges.push(`<path class="dag-edge ${cyclic ? "cycle" : ""}" d="M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}" marker-end="url(#dagArrow${cyclic ? "Cycle" : ""})" />`);
      });
    });
    const nodes = arch.parameters.map((parameter, index) => {
      const pos = positions[index];
      const label = names[index].length > 20 ? `${names[index].slice(0, 18)}…` : names[index];
      const isAudio = arch.audio_dependencies.includes(parameter.name);
      const isCycle = graphInfo.cycleNames.has(parameter.name);
      return `<g class="dag-node ${parameter.type === "DIRECT" ? "direct" : "indirect"} ${isAudio ? "audio" : ""} ${isCycle ? "cycle" : ""}" transform="translate(${pos.x}, ${pos.y})">
        <rect width="${nodeWidth}" height="${nodeHeight}" rx="13" />
        <text class="dag-node-name" x="14" y="25">${escapeHtml(label)}</text>
        <text class="dag-node-type" x="14" y="44">${escapeHtml(parameter.type)}</text>
        ${isAudio ? `<g transform="translate(${nodeWidth - 50}, 35)"><rect class="audio-tag" width="39" height="16" rx="8"/><text class="audio-tag-text" x="19.5" y="11" text-anchor="middle">AUDIO</text></g>` : ""}
      </g>`;
    }).join("");
    return `<svg class="dag-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="参数依赖有向图">
      <defs>
        <marker id="dagArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" /></marker>
        <marker id="dagArrowCycle" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" /></marker>
      </defs>
      ${edges.join("")}${nodes}
    </svg>`;
  }

  function renderSingerSection(arch) {
    const singer = currentSinger();
    return `
      <section class="nested-section singer-section">
        <div class="nested-heading">
          <div>
            <h3>歌手</h3>
            <p>Mock key 稳定决定 env tag、视觉标识、旋律与音色。</p>
          </div>
          <button class="button secondary small" data-action="add-singer" type="button">+ 新增歌手</button>
        </div>
        <div class="singer-tabs" role="tablist" aria-label="歌手列表">
          ${arch.singers.length
            ? arch.singers.map((item, index) => `<button class="singer-tab ${index === state.selectedSinger ? "selected" : ""}" data-select-singer="${index}" type="button" role="tab" aria-selected="${index === state.selectedSinger}"><span class="singer-identicon" style="--seed-hue:${hashHue(item.mock_key)}"></span><span><strong>${escapeHtml(item.name || item.id)}</strong><small>${escapeHtml(item.id)}</small></span></button>`).join("")
            : '<span class="subnav-empty">暂无歌手</span>'}
        </div>
        ${singer ? renderSingerEditor(singer) : `
          <div class="mini-empty singer-empty">添加歌手后，可配置语言、混合组、Mock key 和示例音频。</div>`}
      </section>`;
  }

  function hashHue(value) {
    let hash = 2166136261;
    for (const char of String(value ?? "")) {
      hash ^= char.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return Math.abs(hash) % 360;
  }

  function renderSingerEditor(singer) {
    const languages = singer.languages ?? [];
    const defaultOptions = languages.length
      ? languages.map((language) => `<option value="${escapeHtml(language)}" ${language === singer.default_language ? "selected" : ""}>${escapeHtml(language)}</option>`).join("")
      : '<option value="">请先填写语言列表</option>';
    return `
      <div class="singer-editor">
        <div class="editor-heading subtle">
          <h4>${escapeHtml(singer.name || singer.id)}</h4>
          <button class="button danger-ghost small" data-action="delete-singer" type="button">删除歌手</button>
        </div>
        <div class="form-grid singer-fields">
          ${field("歌手 ID", `<input data-singer-field="id" type="text" value="${escapeHtml(singer.id)}" spellcheck="false" />`)}
          ${field("显示名称", `<input data-singer-field="name" type="text" value="${escapeHtml(singer.name)}" />`)}
          ${field("混合组 ID", `<input data-singer-field="mix_group" type="text" value="${escapeHtml(singer.mix_group)}" spellcheck="false" />`, "只有同一 mix group 中的歌手才应被混合。")}
          ${field("支持语言", `<input data-singer-field="languages" type="text" value="${escapeHtml(languages.join(", "))}" placeholder="zh, en, ja" spellcheck="false" />`, "使用逗号分隔，至少一项。")}
          ${field("默认语言", `<select data-singer-field="default_language">${defaultOptions}</select>`, "必须包含在支持语言中。")}
          ${field("Mock key", `<input data-singer-field="mock_key" type="text" value="${escapeHtml(singer.mock_key)}" spellcheck="false" />`, "相同 key 始终生成相同的标识与音色。", "span-2")}
        </div>
        <div class="demo-section">
          <div class="demo-heading">
            <div><strong>示例音频</strong><span>名称允许为空；旋律由 Mock key 和列表顺序确定。</span></div>
            <button class="button ghost small" data-action="add-demo" type="button">+ 添加示例</button>
          </div>
          <div class="demo-list">
            ${singer.demo_audios.length
              ? singer.demo_audios.map((demo, index) => `<div class="demo-row"><span class="demo-number">${index + 1}</span><input data-demo-index="${index}" type="text" value="${escapeHtml(demo.name)}" placeholder="示例名称（可留空）" /><span class="melody-mark" title="确定性旋律">♪ ${String(index + 1).padStart(2, "0")}</span><button class="row-delete" data-action="delete-demo" data-demo-index="${index}" type="button" title="删除示例" aria-label="删除示例">×</button></div>`).join("")
              : '<div class="mini-empty compact">尚未配置示例音频。</div>'}
          </div>
        </div>
      </div>`;
  }

  function renderLogFilter() {
    const previous = dom.logInstanceFilter.value;
    const options = (state.config?.instances ?? [])
      .map((instance) => `<option value="${escapeHtml(instance.id)}">${escapeHtml(instance.name || instance.id)}</option>`)
      .join("");
    dom.logInstanceFilter.innerHTML = `<option value="">全部实例</option>${options}`;
    if ([...dom.logInstanceFilter.options].some((option) => option.value === previous)) {
      dom.logInstanceFilter.value = previous;
    }
  }

  function logIdentity(log) {
    if (log.id != null) return `id:${log.id}`;
    if (log.sequence != null) return `seq:${log.sequence}`;
    if (log.seq != null) return `seq:${log.seq}`;
    return JSON.stringify([
      log.timestamp ?? log.time,
      log.instance_id ?? log.instance,
      log.method,
      log.path ?? log.url,
      log.status_code ?? log.status,
      log.duration_ms,
    ]);
  }

  function normalizeLogs(payload) {
    if (Array.isArray(payload)) return { items: payload, cursor: null };
    const items = payload?.items ?? payload?.logs ?? payload?.events ?? [];
    const cursor = payload?.next_cursor ?? payload?.cursor ?? payload?.next ?? null;
    return { items: Array.isArray(items) ? items : [], cursor };
  }

  function renderLogs() {
    const filter = dom.logInstanceFilter?.value ?? "";
    const items = filter
      ? state.logs.filter((log) => String(log.instance_id ?? log.instance ?? log.service_id ?? "") === filter)
      : state.logs;
    const visible = [...items]
      .sort((left, right) => logTimeValue(right) - logTimeValue(left))
      .slice(0, 200);
    dom.logSummary.textContent = state.logsPaused
      ? `已暂停 · 当前视图 ${items.length} 条`
      : `自动刷新 · 当前视图 ${items.length} 条${items.length > 200 ? "（显示最新 200 条）" : ""}`;
    if (!visible.length) {
      dom.logList.innerHTML = `
        <div class="logs-empty">
          <span>&lt;/&gt;</span>
          <strong>尚无请求日志</strong>
          <p>调用任一已启动 Mock 实例的 /v1 端点后，请求和响应会出现在这里。</p>
        </div>`;
      return;
    }
    dom.logList.innerHTML = visible.map(renderLogRow).join("");
  }

  function renderLogRow(log) {
    const timestamp = formatTimestamp(log.timestamp ?? log.time ?? log.created_at);
    const method = String(log.method ?? log.request?.method ?? "REQ").toUpperCase();
    const path = log.path ?? log.url ?? log.request?.path ?? "未知路径";
    const instance = log.instance_id ?? log.instance ?? log.service_id ?? "—";
    const status = log.status_code ?? log.response?.status_code ?? log.response?.status ?? log.status ?? "—";
    const duration = log.duration_ms ?? log.elapsed_ms ?? log.duration;
    const requestBody = log.request_body ?? log.request?.body ?? log.request ?? null;
    const responseBody = log.response_body ?? log.response?.body ?? log.response ?? null;
    const numericStatus = Number(status);
    const statusClass = Number.isFinite(numericStatus)
      ? numericStatus >= 500 ? "server-error" : numericStatus >= 400 ? "client-error" : numericStatus >= 300 ? "redirect" : "ok"
      : "neutral";
    return `
      <article class="log-entry">
        <div class="log-line">
          <time>${escapeHtml(timestamp)}</time>
          <span class="method-badge method-${escapeHtml(method.toLowerCase())}">${escapeHtml(method)}</span>
          <code>${escapeHtml(path)}</code>
          <span class="log-spacer"></span>
          <span class="log-instance">${escapeHtml(instance)}</span>
          <span class="response-status ${statusClass}">${escapeHtml(status)}</span>
          ${duration != null ? `<span class="duration">${escapeHtml(Number(duration).toFixed(Number(duration) < 10 ? 1 : 0))} ms</span>` : ""}
          <button class="log-expand" type="button" aria-label="展开日志详情" title="查看请求与响应">›</button>
        </div>
        <div class="log-detail" hidden>
          <div><strong>请求</strong><pre>${escapeHtml(formatPayload(requestBody))}</pre></div>
          <div><strong>响应</strong><pre>${escapeHtml(formatPayload(responseBody))}</pre></div>
        </div>
      </article>`;
  }

  function formatTimestamp(value) {
    if (!value) return new Date().toLocaleTimeString("zh-CN", { hour12: false });
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function logTimeValue(log) {
    const parsed = Date.parse(log.timestamp ?? log.time ?? log.created_at ?? "");
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function formatPayload(value) {
    if (value == null || value === "") return "（空）";
    if (typeof value === "string") {
      try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
    }
    try { return JSON.stringify(value, null, 2); } catch { return String(value); }
  }

  function renderStatuses() {
    renderResourceStatus();
    const instance = currentInstance();
    if (instance) {
      const info = state.instanceStatus.get(instance.id) ?? {};
      const rawStatus = info.status ?? (info.running === true ? "running" : info.running === false ? "stopped" : "unknown");
      const badge = byId("instanceStatusBadge");
      if (badge) {
        badge.className = `status-badge ${statusTone(rawStatus)}`;
        badge.innerHTML = `<i></i>${escapeHtml(statusLabel(rawStatus))}`;
      }
      const details = byId("instanceStatusDetails");
      if (details && (info.error || info.message || info.pid || info.url)) {
        const parts = [info.error ?? info.message, info.pid ? `PID ${info.pid}` : null, info.url].filter(Boolean);
        details.textContent = parts.join(" · ");
      }
    }
    document.querySelectorAll("[data-status-id]").forEach((element) => {
      const info = state.instanceStatus.get(element.dataset.statusId) ?? {};
      const rawStatus = info.status ?? (info.running === true ? "running" : info.running === false ? "stopped" : "unknown");
      element.className = `status-dot ${statusTone(rawStatus)}`;
      element.title = statusLabel(rawStatus);
    });
  }

  function renderResourceStatus() {
    const badge = byId("resourceStatusBadge");
    const details = byId("resourceStatusDetails");
    if (!badge || !details) return;
    const info = state.resourceStatus ?? {};
    const rawStatus = info.status ?? (info.running === true ? "running" : info.running === false ? "stopped" : "unknown");
    const allDataUrl = (state.config?.instances ?? []).every((instance) => instance.media_mode === "data_url");
    const unused = String(rawStatus).toLowerCase() === "stopped" && allDataUrl;
    const label = unused ? "未启用" : statusLabel(rawStatus);
    const tone = unused ? "idle" : statusTone(rawStatus);
    badge.className = `status-badge ${tone}`;
    badge.innerHTML = `<i></i>${escapeHtml(label)}`;

    const configuredHost = info.host ?? state.config?.resource_host;
    const configuredPort = info.port ?? state.config?.resource_port;
    const fallbackUrl = configuredHost && configuredPort
      ? `http://${configuredHost}:${configuredPort}`
      : null;
    const url = info.url ?? state.config?.resource_public_base_url ?? fallbackUrl;
    const parts = [];
    if (url) parts.push(url);
    if (info.error) parts.push(info.error);
    else if (unused) parts.push("全部实例使用 Data URL 模式，无需共享资源服务");
    else if (String(rawStatus).toLowerCase() === "stopped") parts.push("共享资源服务尚未运行");
    details.textContent = parts.join(" · ") || "尚未获取资源服务状态";
  }

  function validateIdentifier(value, label, issues) {
    if (!String(value ?? "").trim()) issues.push(`${label}不能为空`);
    else if (/[\\/]/.test(String(value))) issues.push(`${label}不能包含斜杠`);
  }

  function validateInstance(instance, instanceIndex) {
    const issues = [];
    const prefix = `实例 ${instanceIndex + 1}`;
    validateIdentifier(instance.id, `${prefix} ID`, issues);
    if (!String(instance.name ?? "").trim()) issues.push(`${prefix}名称不能为空`);
    if (!String(instance.host ?? "").trim()) issues.push(`${prefix}监听主机不能为空`);
    if (!Number.isInteger(Number(instance.port)) || Number(instance.port) < 1 || Number(instance.port) > 65535) issues.push(`${prefix}端口必须在 1–65535 之间`);
    if (!(Number(instance.parameter_sample_rate) > 0 && Number(instance.parameter_sample_rate) <= 10000)) issues.push(`${prefix}参数采样率必须在 0–10000 Hz 之间`);
    if (!Number.isInteger(Number(instance.resource_ttl_seconds)) || Number(instance.resource_ttl_seconds) < 1 || Number(instance.resource_ttl_seconds) > 86400) issues.push(`${prefix}资源 TTL 必须在 1–86400 秒之间`);
    const archIds = new Set();
    const mockKeys = new Set();
    instance.architectures.forEach((arch, archIndex) => {
      const archLabel = `Arch ${archIndex + 1}`;
      validateIdentifier(arch.id, `${archLabel} ID`, issues);
      const normalizedArchId = String(arch.id ?? "").trim();
      if (archIds.has(normalizedArchId)) issues.push(`Arch ID“${normalizedArchId}”重复`);
      archIds.add(normalizedArchId);
      if (!String(arch.name ?? "").trim()) issues.push(`${archLabel}名称不能为空`);
      issues.push(...inspectGraph(arch).errors.map((item) => `${arch.id || archLabel}：${item}`));
      arch.parameters.forEach((parameter) => {
        const parameterLabel = `${arch.id || archLabel} / 参数“${parameter.name || "（未命名）"}”`;
        const minValue = parameter.min_value;
        const maxValue = parameter.max_value;
        if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
          issues.push(`${parameterLabel}的最小值和最大值必须是有限数字`);
        } else if (minValue >= maxValue) {
          issues.push(`${parameterLabel}的最小值必须小于最大值`);
        }
        if (parameter.name === "pitch" && (minValue !== 0 || maxValue !== 12800)) {
          issues.push(`${arch.id || archLabel} / pitch 的数值范围必须固定为 0–12800`);
        }
      });
      const parameterNames = new Set(arch.parameters.map((item) => item.name));
      arch.audio_dependencies.forEach((name) => {
        if (!parameterNames.has(name)) issues.push(`${arch.id || archLabel}：音频依赖“${name}”不存在`);
      });
      if (new Set(arch.audio_dependencies).size !== arch.audio_dependencies.length) issues.push(`${arch.id || archLabel}：音频依赖不能重复`);
      const singerIds = new Set();
      arch.singers.forEach((singer, singerIndex) => {
        const singerLabel = `${arch.id || archLabel} / 歌手 ${singerIndex + 1}`;
        validateIdentifier(singer.id, `${singerLabel} ID`, issues);
        const normalizedSingerId = String(singer.id ?? "").trim();
        if (singerIds.has(normalizedSingerId)) issues.push(`${arch.id || archLabel}：歌手 ID“${normalizedSingerId}”重复`);
        singerIds.add(normalizedSingerId);
        if (!String(singer.name ?? "").trim()) issues.push(`${singerLabel}名称不能为空`);
        if (!String(singer.mix_group ?? "").trim()) issues.push(`${singerLabel}混合组不能为空`);
        if (!String(singer.mock_key ?? "").trim()) issues.push(`${singerLabel} Mock key 不能为空`);
        const normalizedMockKey = String(singer.mock_key ?? "").trim();
        if (mockKeys.has(normalizedMockKey)) issues.push(`${prefix}中 Mock key“${normalizedMockKey}”重复`);
        mockKeys.add(normalizedMockKey);
        if (!singer.languages.length) issues.push(`${singerLabel}至少需要一种语言`);
        if (new Set(singer.languages).size !== singer.languages.length) issues.push(`${singerLabel}语言不能重复`);
        if (!singer.languages.includes(singer.default_language)) issues.push(`${singerLabel}默认语言必须位于语言列表中`);
      });
    });
    return [...new Set(issues)];
  }

  function validateConfig() {
    const issues = [];
    const config = state.config;
    if (!config) return ["配置尚未加载"];
    if (!String(config.resource_host ?? "").trim()) issues.push("资源服务监听主机不能为空");
    if (!Number.isInteger(Number(config.resource_port)) || Number(config.resource_port) < 1 || Number(config.resource_port) > 65535) issues.push("资源服务端口必须在 1–65535 之间");
    if (config.resource_public_base_url && !/^https?:\/\//i.test(config.resource_public_base_url)) issues.push("公开基础 URL 必须使用 http:// 或 https://");
    const instanceIds = new Set();
    const bindings = new Set();
    config.instances.forEach((instance, index) => {
      issues.push(...validateInstance(instance, index));
      const normalizedInstanceId = String(instance.id ?? "").trim();
      if (instanceIds.has(normalizedInstanceId)) issues.push(`实例 ID“${normalizedInstanceId}”重复`);
      instanceIds.add(normalizedInstanceId);
      const binding = `${String(instance.host ?? "").trim()}:${instance.port}`;
      if (bindings.has(binding)) issues.push(`多个实例使用了相同监听地址 ${binding}`);
      bindings.add(binding);
      if (String(instance.host ?? "").trim() === String(config.resource_host ?? "").trim() && Number(instance.port) === Number(config.resource_port)) issues.push(`实例“${instance.id}”与资源服务使用了相同监听地址`);
    });
    return [...new Set(issues)];
  }

  async function apiFetch(url, options = {}) {
    const response = await fetch(url, {
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers ?? {}) },
      cache: "no-store",
      ...options,
    });
    let payload = null;
    const contentType = response.headers.get("content-type") ?? "";
    if (response.status !== 204) {
      try {
        payload = contentType.includes("json") ? await response.json() : await response.text();
      } catch {
        payload = null;
      }
    }
    if (!response.ok) {
      const error = new Error(formatApiError(response.status, payload));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function formatApiError(status, payload) {
    if (typeof payload === "string" && payload.trim()) return `${status} · ${payload}`;
    if (payload && typeof payload === "object") {
      const title = payload.title ?? payload.error ?? `HTTP ${status}`;
      const detail = payload.detail;
      if (Array.isArray(detail)) {
        const messages = detail.map((item) => `${Array.isArray(item.loc) ? item.loc.join(".") : ""} ${item.msg ?? ""}`.trim());
        return `${title}：${messages.join("；")}`;
      }
      return detail ? `${title}：${detail}` : String(title);
    }
    return `请求失败（HTTP ${status}）`;
  }

  async function loadConfig(force = false) {
    if (state.dirty && !force) {
      const confirmed = await askConfirm("放弃未保存的更改？", "将重新从服务器读取配置，当前未保存的更改会丢失。", "放弃并重载");
      if (!confirmed) return;
    }
    state.loading = true;
    dom.saveButton.disabled = true;
    setSaveState("正在读取配置…");
    try {
      const payload = await apiFetch(API.config);
      state.config = normalizeConfig(payload);
      state.dirty = false;
      state.selectedInstance = 0;
      state.selectedArchitecture = 0;
      state.selectedSinger = 0;
      setSaveState("已与持久化配置同步", "success");
      renderAll();
      await refreshStatus();
    } catch (error) {
      setSaveState("配置读取失败", "error");
      showToast("无法读取配置", error.message, "error", 7000);
      if (!state.config) {
        state.config = normalizeConfig({ instances: [] });
        renderAll();
      }
    } finally {
      state.loading = false;
      dom.saveButton.disabled = !state.dirty;
    }
  }

  async function saveConfig() {
    const issues = validateConfig();
    if (issues.length) {
      showToast("配置未保存", `${issues[0]}${issues.length > 1 ? `（另有 ${issues.length - 1} 项）` : ""}`, "error", 7000);
      setSaveState(`需修正 ${issues.length} 个配置问题`, "error");
      renderWorkspace();
      return;
    }
    state.saving = true;
    dom.saveButton.disabled = true;
    setSaveState("正在校验并持久化…");
    try {
      const payload = await apiFetch(API.config, { method: "PUT", body: JSON.stringify(state.config) });
      if (payload) state.config = normalizeConfig(payload);
      state.dirty = false;
      setSaveState("配置已保存", "success");
      showToast("保存成功", "持久化配置已更新。已运行实例如需应用绑定变更，请重启。", "success");
      renderAll();
    } catch (error) {
      setSaveState("保存失败", "error");
      showToast("配置保存失败", error.message, "error", 8000);
    } finally {
      state.saving = false;
      dom.saveButton.disabled = !state.dirty;
    }
  }

  async function refreshStatus() {
    if (!state.config) return;
    try {
      const payload = await apiFetch(API.status);
      const normalized = normalizeStatus(payload);
      state.instanceStatus = normalized.instances;
      state.resourceStatus = normalized.resource;
      renderStatuses();
    } catch (error) {
      if (error.status !== 404) console.warn("status polling failed", error);
    }
  }

  async function pollLogs() {
    if (state.logsPaused) return;
    const params = new URLSearchParams();
    if (state.logCursor != null) params.set("after", String(state.logCursor));
    params.set("limit", "100");
    try {
      const payload = await apiFetch(`${API.logs}?${params}`);
      const normalized = normalizeLogs(payload);
      normalized.items.forEach((log) => {
        const identity = logIdentity(log);
        if (!state.seenLogs.has(identity)) {
          state.seenLogs.add(identity);
          state.logs.push(log);
        }
      });
      if (normalized.cursor != null) state.logCursor = normalized.cursor;
      else if (normalized.items.length) {
        // The administration API returns newest-first entries. Its `after` cursor
        // therefore needs the newest ID, so the next poll only returns later items.
        const newest = normalized.items[0];
        state.logCursor = newest.sequence ?? newest.seq ?? newest.id ?? state.logCursor;
      }
      if (state.logs.length > 500) {
        state.logs.sort((left, right) => logTimeValue(right) - logTimeValue(left));
        state.logs.length = 500;
      }
      renderLogs();
    } catch (error) {
      if (error.status !== 404) console.warn("log polling failed", error);
    }
  }

  async function controlInstance(action) {
    const instance = currentInstance();
    if (!instance || state.controlBusy) return;
    if (state.dirty) {
      showToast("请先保存配置", "实例控制使用服务器上已持久化的配置。", "warning");
      return;
    }
    state.controlBusy = true;
    document.querySelectorAll("[data-instance-action]").forEach((button) => { button.disabled = true; });
    try {
      await apiFetch(`/api/instances/${encodeURIComponent(instance.id)}/${action}`, { method: "POST" });
      showToast(`已发送${{ start: "启动", stop: "停止", restart: "重启" }[action]}指令`, instance.name || instance.id, "success");
      await refreshStatus();
    } catch (error) {
      showToast("实例操作失败", error.message, "error", 7000);
    } finally {
      state.controlBusy = false;
      document.querySelectorAll("[data-instance-action]").forEach((button) => { button.disabled = false; });
    }
  }

  function showToast(title, message, tone = "neutral", duration = 4500) {
    const toast = document.createElement("div");
    toast.className = `toast ${tone}`;
    toast.innerHTML = `<span class="toast-icon">${tone === "success" ? "✓" : tone === "error" ? "!" : tone === "warning" ? "!" : "i"}</span><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message ?? "")}</p></div><button type="button" aria-label="关闭通知">×</button>`;
    const remove = () => {
      toast.classList.add("leaving");
      window.setTimeout(() => toast.remove(), 180);
    };
    toast.querySelector("button").addEventListener("click", remove);
    dom.toastRegion.append(toast);
    window.setTimeout(remove, duration);
  }

  function askConfirm(title, message, acceptLabel = "删除") {
    if (!dom.confirmDialog?.showModal) return Promise.resolve(window.confirm(message));
    dom.confirmTitle.textContent = title;
    dom.confirmMessage.textContent = message;
    dom.confirmAccept.textContent = acceptLabel;
    dom.confirmDialog.showModal();
    return new Promise((resolve) => {
      dom.confirmDialog.addEventListener("close", () => resolve(dom.confirmDialog.returnValue === "confirm"), { once: true });
    });
  }

  function updateResourceField(target) {
    const key = target.dataset.resourceField;
    if (!key || !state.config) return;
    if (key === "resource_port") state.config[key] = toNumber(target.value);
    else if (key === "resource_public_base_url") state.config[key] = target.value.trim() || null;
    else state.config[key] = target.value;
    markDirty();
  }

  function updateWorkspaceField(target, eventType) {
    const instance = currentInstance();
    const arch = currentArchitecture();
    const singer = currentSinger();
    if (!instance) return false;

    if (target.dataset.instanceField) {
      const key = target.dataset.instanceField;
      if (target.type === "checkbox") instance[key] = target.checked;
      else if (["port", "parameter_sample_rate", "resource_ttl_seconds"].includes(key)) instance[key] = toNumber(target.value);
      else instance[key] = target.value;
      markDirty();
      if (eventType === "change" && key === "media_mode") {
        renderWorkspace();
        renderResourceStatus();
      }
      if (eventType === "change" && ["id", "name", "host", "port"].includes(key)) renderInstanceList();
      return true;
    }
    if (target.dataset.archField && arch) {
      arch[target.dataset.archField] = target.value;
      markDirty();
      if (eventType === "change") {
        renderInstanceList();
        renderWorkspace();
      }
      return true;
    }
    if (target.dataset.parameterField && arch) {
      const index = Number(target.dataset.parameterIndex);
      const parameter = arch.parameters[index];
      if (!parameter) return true;
      const key = target.dataset.parameterField;
      if (key === "name") {
        const oldName = parameter.name;
        const newName = target.value.trim();
        const wasPitch = oldName === "pitch";
        parameter.name = newName;
        arch.parameters.forEach((item, itemIndex) => {
          if (itemIndex !== index) item.depends_on = item.depends_on.map((name) => name === oldName ? newName : name);
        });
        arch.audio_dependencies = arch.audio_dependencies.map((name) => name === oldName ? newName : name);
        if (newName === "pitch") {
          parameter.min_value = 0;
          parameter.max_value = 12800;
        } else if (wasPitch && Number(parameter.min_value) === 0 && Number(parameter.max_value) === 12800) {
          parameter.min_value = -1000;
          parameter.max_value = 1000;
        }
        if (wasPitch !== (newName === "pitch")) syncParameterRangeControls(target, parameter);
      } else if (key === "min_value" || key === "max_value") {
        parameter[key] = toNumber(target.value);
      } else {
        parameter.type = target.value;
        if (parameter.type === "DIRECT") parameter.depends_on = [];
      }
      markDirty();
      if (eventType === "change") renderWorkspace();
      return true;
    }
    if (target.dataset.parameterDependency !== undefined && arch) {
      const parameter = arch.parameters[Number(target.dataset.parameterIndex)];
      const dependency = target.dataset.parameterDependency;
      if (target.checked && !parameter.depends_on.includes(dependency)) parameter.depends_on.push(dependency);
      if (!target.checked) parameter.depends_on = parameter.depends_on.filter((item) => item !== dependency);
      markDirty();
      renderWorkspace();
      return true;
    }
    if (target.dataset.audioDependency !== undefined && arch) {
      const dependency = target.dataset.audioDependency;
      if (target.checked && !arch.audio_dependencies.includes(dependency)) arch.audio_dependencies.push(dependency);
      if (!target.checked) arch.audio_dependencies = arch.audio_dependencies.filter((item) => item !== dependency);
      markDirty();
      renderWorkspace();
      return true;
    }
    if (target.dataset.singerField && singer) {
      const key = target.dataset.singerField;
      if (key === "languages") {
        singer.languages = splitLanguages(target.value);
        if (!singer.languages.includes(singer.default_language)) singer.default_language = singer.languages[0] ?? "";
      } else singer[key] = target.value;
      markDirty();
      if (eventType === "change" && ["languages", "id", "name", "mock_key"].includes(key)) renderWorkspace();
      return true;
    }
    if (target.dataset.demoIndex !== undefined && singer && !target.dataset.action) {
      const demo = singer.demo_audios[Number(target.dataset.demoIndex)];
      if (demo) demo.name = target.value;
      markDirty();
      return true;
    }
    return false;
  }

  async function handleWorkspaceClick(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.selectArchitecture !== undefined) {
      state.selectedArchitecture = Number(button.dataset.selectArchitecture);
      state.selectedSinger = 0;
      renderWorkspace();
      return;
    }
    if (button.dataset.selectSinger !== undefined) {
      state.selectedSinger = Number(button.dataset.selectSinger);
      renderWorkspace();
      return;
    }
    if (button.dataset.instanceAction) {
      await controlInstance(button.dataset.instanceAction);
      return;
    }
    const action = button.dataset.action;
    if (!action) return;
    const instance = currentInstance();
    const arch = currentArchitecture();
    const singer = currentSinger();
    if (action === "add-instance") {
      state.config.instances.push(newInstance());
      state.selectedInstance = state.config.instances.length - 1;
      state.selectedArchitecture = 0;
      state.selectedSinger = 0;
      markDirty();
      renderAll();
    } else if (action === "delete-instance" && instance) {
      const confirmed = await askConfirm("删除 Mock 实例？", `将从配置中删除“${instance.name || instance.id}”及其全部 Arch 和歌手。保存配置后生效。`);
      if (!confirmed) return;
      state.config.instances.splice(state.selectedInstance, 1);
      state.selectedInstance = Math.max(0, state.selectedInstance - 1);
      state.selectedArchitecture = 0;
      state.selectedSinger = 0;
      markDirty();
      renderAll();
    } else if (action === "add-architecture" && instance) {
      instance.architectures.push(newArchitecture(instance));
      state.selectedArchitecture = instance.architectures.length - 1;
      state.selectedSinger = 0;
      markDirty();
      renderWorkspace();
    } else if (action === "delete-architecture" && arch) {
      const confirmed = await askConfirm("删除 Arch？", `将删除“${arch.name || arch.id}”、${arch.parameters.length} 个参数和 ${arch.singers.length} 位歌手。`);
      if (!confirmed) return;
      instance.architectures.splice(state.selectedArchitecture, 1);
      state.selectedArchitecture = Math.max(0, state.selectedArchitecture - 1);
      state.selectedSinger = 0;
      markDirty();
      renderWorkspace();
    } else if (action === "add-parameter" && arch) {
      arch.parameters.push(newParameter(arch));
      markDirty();
      renderWorkspace();
    } else if (action === "delete-parameter" && arch) {
      const index = Number(button.dataset.parameterIndex);
      const removed = arch.parameters[index];
      if (!removed) return;
      arch.parameters.splice(index, 1);
      arch.parameters.forEach((item) => { item.depends_on = item.depends_on.filter((name) => name !== removed.name); });
      arch.audio_dependencies = arch.audio_dependencies.filter((name) => name !== removed.name);
      markDirty();
      renderWorkspace();
    } else if (action === "add-singer" && arch) {
      arch.singers.push(newSinger(arch));
      state.selectedSinger = arch.singers.length - 1;
      markDirty();
      renderWorkspace();
    } else if (action === "delete-singer" && arch && singer) {
      const confirmed = await askConfirm("删除歌手？", `将删除“${singer.name || singer.id}”及其示例音频配置。`);
      if (!confirmed) return;
      arch.singers.splice(state.selectedSinger, 1);
      state.selectedSinger = Math.max(0, state.selectedSinger - 1);
      markDirty();
      renderWorkspace();
    } else if (action === "add-demo" && singer) {
      singer.demo_audios.push({ name: "" });
      markDirty();
      renderWorkspace();
    } else if (action === "delete-demo" && singer) {
      singer.demo_audios.splice(Number(button.dataset.demoIndex), 1);
      markDirty();
      renderWorkspace();
    }
  }

  function bindEvents() {
    dom.resourceFields.addEventListener("input", (event) => updateResourceField(event.target));
    dom.instanceList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-select-instance]");
      if (!button) return;
      state.selectedInstance = Number(button.dataset.selectInstance);
      state.selectedArchitecture = 0;
      state.selectedSinger = 0;
      renderInstanceList();
      renderWorkspace();
    });
    dom.instanceWorkspace.addEventListener("input", (event) => updateWorkspaceField(event.target, "input"));
    dom.instanceWorkspace.addEventListener("change", (event) => updateWorkspaceField(event.target, "change"));
    dom.instanceWorkspace.addEventListener("click", handleWorkspaceClick);
    dom.addInstanceButton.addEventListener("click", () => {
      if (!state.config) return;
      state.config.instances.push(newInstance());
      state.selectedInstance = state.config.instances.length - 1;
      state.selectedArchitecture = 0;
      state.selectedSinger = 0;
      markDirty();
      renderAll();
    });
    dom.saveButton.addEventListener("click", saveConfig);
    dom.reloadButton.addEventListener("click", () => loadConfig(false));
    dom.logInstanceFilter.addEventListener("change", renderLogs);
    dom.pauseLogsButton.addEventListener("click", () => {
      state.logsPaused = !state.logsPaused;
      dom.pauseLogsButton.textContent = state.logsPaused ? "继续刷新" : "暂停刷新";
      renderLogs();
      if (!state.logsPaused) pollLogs();
    });
    dom.clearLogsButton.addEventListener("click", () => {
      state.logs = [];
      state.seenLogs.clear();
      renderLogs();
    });
    dom.logList.addEventListener("click", (event) => {
      const button = event.target.closest(".log-expand");
      if (!button) return;
      const detail = button.closest(".log-entry").querySelector(".log-detail");
      detail.hidden = !detail.hidden;
      button.classList.toggle("expanded", !detail.hidden);
      button.setAttribute("aria-expanded", String(!detail.hidden));
    });
    window.addEventListener("beforeunload", (event) => {
      if (!state.dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (state.dirty && !state.saving) saveConfig();
      }
    });
  }

  function startPolling() {
    state.statusTimer = window.setInterval(refreshStatus, 2500);
    state.logTimer = window.setInterval(pollLogs, 1800);
    pollLogs();
  }

  function init() {
    Object.assign(dom, {
      saveState: byId("saveState"),
      saveButton: byId("saveButton"),
      reloadButton: byId("reloadButton"),
      resourceFields: byId("resourceFields"),
      instanceList: byId("instanceList"),
      instanceWorkspace: byId("instanceWorkspace"),
      addInstanceButton: byId("addInstanceButton"),
      logInstanceFilter: byId("logInstanceFilter"),
      pauseLogsButton: byId("pauseLogsButton"),
      clearLogsButton: byId("clearLogsButton"),
      logSummary: byId("logSummary"),
      logList: byId("logList"),
      toastRegion: byId("toastRegion"),
      confirmDialog: byId("confirmDialog"),
      confirmTitle: byId("confirmTitle"),
      confirmMessage: byId("confirmMessage"),
      confirmAccept: byId("confirmAccept"),
    });
    bindEvents();
    loadConfig(true);
    startPolling();
  }

  init();
})();
