/* 工作台里的四步制作向导。
   目标是让普通用户只做四件事：导入素材、训练、试听、选国家。
   复杂的逐句编辑仍然可以在“高级编辑”里完成。 */
(() => {
  "use strict";

  const state = {
    options: null,
    projects: [],
    project: null,
    pack: null,
    prepare: null,
    lines: [],
    bankCounts: {},
    selectedNations: new Set(),
    selectedBanks: new Set(),
    cockpitSelection: null,
    mode: "zh",
    categories: [],
    filter: "key",
    search: "",
    limit: 60,
    busy: false,
    actionBusy: false,
    initialized: false,
    previewUrl: "",
    lineBusy: "",
    downloadSignature: "",
    exportInfo: null,
    installInfo: null,
    componentTimer: null,
    quickStep: "quick-step-1",
    preparedSignature: "",
  };

  const COACH_COPY = {
    "quick-step-1": {
      title: "先放一段干净的人声",
      text: "3–10 秒、没有背景音乐。导入后点 ▶ 试听，不顺手的素材直接删掉。",
    },
    "quick-step-2": {
      title: "零样本也能直接往下走",
      text: "想更像本人再点训练；不训练也能先去生成语音，先做一版听听。",
    },
    "quick-step-3": {
      title: "先点场景，再一键试听",
      text: "点卡片加入空战 / 陆战 / 海战 / 座舱，可以多选叠加；范围和各国语音表会自动选好。",
    },
    "quick-step-4": {
      title: "最后一步，打包进游戏",
      text: "先关掉游戏。覆盖同名 bank 会先备份；不满意可以一键恢复安装前状态。",
    },
  };

  const playback = {
    active: false,
    queue: [],
    index: 0,
    currentId: "",
    lastLine: null,
    lastIndex: 0,
    resumeId: "",
    single: false,
    failures: 0,
    loop: localStorage.getItem("wt.quick.loop") !== "0",
  };

  const KEY_CATEGORIES = new Set([
    "击杀/战果",
    "告警/RWR",
    "告警/导弹",
    "损伤/灭火",
    "装填/弹药",
    "乘员",
    "火力支援",
    "发动机/仪表",
    "起落架/襟翼",
    "无线电",
    "座舱告警",
  ]);

  const el = (id) => document.getElementById(id);
  const escape = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);

  const toastMessage = (message, tone = "ok") => {
    if (typeof toast === "function") toast(message, tone);
    else console.log(message);
  };

  const isOriginalMode = () => state.pack?.quick?.mode === "original";

  const isMissingText = (line) =>
    line?.text_source !== "non_voice" &&
    line?.text_source !== "original" &&
    !String(line?.text || "").trim();

  const needsSynthesis = (line) =>
    !isOriginalMode() &&
    line?.text_source !== "non_voice" &&
    Boolean(String(line?.text || "").trim()) &&
    (!line.audio || line.status !== "ready");

  function recordingVariant(line) {
    const name = String(line?.source?.event || line?.event || "");
    const match = /_(\d+)_(\d+)$/.exec(name);
    if (!match) return "";
    if (!/(reloading|loading|repair|extinguish)/i.test(name)) return "";
    return `录音变体 ${match[1]}-${match[2]}`;
  }

  const needsConversion = (line) =>
    isOriginalMode() &&
    line?.text_source === "original" &&
    (!line.audio || line.status !== "ready");

  const setPill = (node, text, tone = "") => {
    if (!node) return;
    node.textContent = text;
    node.className = `pill ${tone}`.trim();
  };

  async function apiCall(path, options = {}) {
    if (typeof api === "function") return api(path, options);
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
    return payload;
  }

  function formatBytes(value) {
    const size = Number(value) || 0;
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
    return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function humanMinutes(lines) {
    if (!lines) return "—";
    const seconds = lines * 3.5;
    if (seconds < 90) return `${Math.max(1, Math.round(seconds / 60))} 分钟`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 90) return `${minutes} 分钟`;
    return `${(minutes / 60).toFixed(1)} 小时`;
  }

  function selectedLineScopes() {
    return ["common", "air", "other", "ground", "naval", "cockpit"].filter(
      (scope) => el(`quick-scope-${scope}`)?.checked,
    );
  }

  function selectedBankScopes() {
    const lineScopes = new Set(selectedLineScopes());
    const result = [];
    if (lineScopes.has("common") || lineScopes.has("air") || lineScopes.has("other")) {
      result.push("common");
    }
    if (lineScopes.has("ground")) result.push("ground");
    if (lineScopes.has("naval")) result.push("naval");
    if (lineScopes.has("cockpit")) result.push("cockpit");
    return result;
  }

  function cockpitInfo() {
    return state.options?.cockpit || null;
  }

  function cockpitOn() {
    return selectedLineScopes().includes("cockpit");
  }

  function cockpitItems() {
    return cockpitInfo()?.items || [];
  }

  function ensureCockpitSelection() {
    const items = cockpitItems();
    if (!items.length) return;
    if (!(state.cockpitSelection instanceof Set)) {
      const saved = state.pack?.quick?.cockpit_events;
      state.cockpitSelection = Array.isArray(saved)
        ? new Set(saved)
        : new Set(items.filter((item) => item.default_selected).map((item) => item.event_id));
    }
  }

  function selectedCockpitItems() {
    ensureCockpitSelection();
    if (!(state.cockpitSelection instanceof Set)) return [];
    return cockpitItems().filter((item) => state.cockpitSelection.has(item.event_id));
  }

  function cockpitVoiceCount() {
    return selectedCockpitItems().length;
  }

  function cockpitVariantCount() {
    return selectedCockpitItems().reduce(
      (total, item) => total + Number(item.variant_count || 0),
      0,
    );
  }

  function lineScopeLabel(banks) {
    const line = new Set(selectedLineScopes());
    const labels = [];
    if ((banks || []).some((bank) => bank.scope === "common")) {
      const common = [];
      if (line.has("common")) common.push("通用");
      if (line.has("air")) common.push("空战");
      if (line.has("other")) common.push("其他");
      if (common.length) labels.push(common.join("+"));
    }
    if ((banks || []).some((bank) => bank.scope === "ground") && line.has("ground")) {
      labels.push("陆战");
    }
    if ((banks || []).some((bank) => bank.scope === "naval") && line.has("naval")) {
      labels.push("海战");
    }
    return labels.join(" + ");
  }

  function modeInfo() {
    return (state.options?.modes || []).find((item) => item.id === state.mode) || {
      id: "zh",
      label: "中文配音",
      note: "",
    };
  }

  function availableBanks() {
    const options = state.options?.nations || [];
    const scopes = new Set(selectedBankScopes());
    const result = [];
    const seen = new Set();
    for (const nation of options) {
      if (!state.selectedNations.has(nation.id)) continue;
      for (const bank of nation.banks || []) {
        if (!scopes.has(bank.scope) || seen.has(bank.bank) || !bank.available) continue;
        seen.add(bank.bank);
        result.push({ ...bank, nation: nation.label, nationId: nation.id });
      }
    }
    const all = state.options?.languages || [];
    const byBank = new Map(all.map((item) => [item.bank, item]));
    for (const bankId of state.selectedBanks) {
      const bank = byBank.get(bankId);
      if (!bank || !scopes.has(bank.scope) || seen.has(bankId)) continue;
      seen.add(bankId);
      result.push({ ...bank, nation: "自定义", nationId: "custom", custom: true });
    }
    return result;
  }

  function totalBankLines(banks) {
    return banks.reduce((sum, item) => sum + Number(state.bankCounts[item.bank] || 0), 0);
  }

  function uniqueTextCount(lines = state.lines) {
    return new Set(
      lines.map((line) => String(line.text || "").trim()).filter(Boolean),
    ).size;
  }

  function progress(prefix, job, textId = "") {
    const wrap = el(`${prefix}-progress`);
    const fill = el(`${prefix}-fill`);
    const text = el(textId || `${prefix}-text`);
    if (!wrap) return;
    if (!job) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    const percent = Number(job.percent ?? 0);
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    if (text) {
      const elapsed = job.elapsed != null ? ` · 已用 ${formatEta(job.elapsed)}` : "";
      text.textContent = `${job.message || "处理中…"}${job.percent == null ? "" : ` · ${percent.toFixed(0)}%`}${elapsed}`;
    }
  }

  function waitJob(initial, onProgress) {
    return new Promise((resolve, reject) => {
      let timer = null;
      const tick = async () => {
        let job = null;
        try {
          const data = await apiCall("/api/jobs/current");
          job = data.job;
        } catch {
          return;
        }
        if (!job) return;
        if (onProgress) onProgress(job);
        if (job.active) return;
        clearInterval(timer);
        if (job.state === "done") resolve(job);
        else if (job.state === "cancelled") reject(new Error("任务已取消"));
        else reject(new Error(job.error || "任务失败"));
      };
      tick();
      timer = setInterval(tick, 1500);
    });
  }

  async function startJob(path, body, onProgress) {
    const data = await apiCall(path, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (onProgress) onProgress(data.job);
    return waitJob(data.job, onProgress);
  }

  function renderEngine(engine) {
    const pill = el("quick-engine-pill");
    const box = el("quick-engine-box");
    if (engine?.available) {
      setPill(pill, engine.running ? "GPT-SoVITS 运行中" : "GPT-SoVITS 已就绪", "ok");
      if (box) box.hidden = true;
      return;
    }
    setPill(pill, "GPT-SoVITS 未安装", "warn");
    if (box && !state.componentTimer) box.hidden = false;
  }

  async function loadEngine() {
    try {
      const data = await apiCall("/api/voice/engine");
      renderEngine(data.engine);
      if (!data.engine?.available && !state.componentTimer) resumeEngineJob();
    } catch {
      renderEngine(null);
      if (!state.componentTimer) resumeEngineJob();
    }
  }

  function engineJobText(job) {
    if (job.state === "downloading") {
      const percent = Number(job.percent || 0);
      return `下载 GPT-SoVITS ${percent.toFixed(1)}%` +
        (job.speed ? ` · ${formatSpeed(job.speed)}` : "") +
        (job.eta ? ` · 剩余 ${formatEta(job.eta)}` : "");
    }
    if (job.state === "verifying") {
      return `SHA-256 校验 ${Number(job.phase_percent ?? 0).toFixed(0)}%`;
    }
    if (job.state === "extracting") {
      return job.phase_percent == null
        ? "解压中…"
        : `解压 ${Number(job.phase_percent).toFixed(0)}%`;
    }
    if (job.state === "done") return "安装完成";
    if (job.state === "error") return `失败：${job.error || job.message || ""}`;
    if (job.state === "cancelled") return job.message || "已取消";
    return job.message || "准备中…";
  }

  function renderEngineJob(job) {
    const wrap = el("quick-engine-progress");
    const fill = el("quick-engine-fill");
    const text = el("quick-engine-text");
    const cancel = el("quick-engine-cancel");
    const button = el("quick-engine-install");
    if (!wrap) return;
    if (!job) {
      wrap.hidden = true;
      if (cancel) cancel.hidden = true;
      if (button) {
        button.disabled = false;
        button.textContent = "一键下载并安装";
      }
      return;
    }
    wrap.hidden = false;
    const percent = Number(
      job.state === "verifying" || job.state === "extracting"
        ? job.phase_percent ?? 0
        : job.percent ?? 0,
    );
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    if (text) text.textContent = engineJobText(job);
    if (cancel) cancel.hidden = !job.active;
    if (button) {
      button.disabled = Boolean(job.active);
      button.textContent = job.active
        ? "正在安装…"
        : job.state === "error" || job.state === "cancelled"
          ? "重试（续传）"
          : "一键下载并安装";
    }
  }

  function pollEngineJob(initial) {
    clearInterval(state.componentTimer);
    const tick = async () => {
      try {
        const data = await apiCall("/api/components/job");
        const job = data.job || initial;
        if (!job) return;
        if (el("quick-engine-box")) el("quick-engine-box").hidden = false;
        renderEngineJob(job);
        if (job.active) return;
        clearInterval(state.componentTimer);
        state.componentTimer = null;
        await loadEngine();
        if (job.state === "done") {
          toastMessage("GPT-SoVITS 已安装");
          loadOptions();
        } else if (job.state === "error") {
          toastMessage(job.error || "GPT-SoVITS 安装失败", "bad");
        }
        setTimeout(() => renderEngineJob(null), 3000);
      } catch {
        /* 下一轮再试 */
      }
    };
    tick();
    state.componentTimer = setInterval(tick, 2000);
  }

  async function resumeEngineJob() {
    try {
      const data = await apiCall("/api/components/job");
      if (data.job?.active) {
        if (el("quick-engine-box")) el("quick-engine-box").hidden = false;
        pollEngineJob(data.job);
      }
    } catch {
      /* 没有安装任务时忽略 */
    }
  }

  async function installEngine() {
    if (state.componentTimer) return;
    if (!confirm("下载 GPT-SoVITS v4 Windows 整合包约 7.5 GB，解压还要预留空间，装到数据目录。继续？")) {
      return;
    }
    const button = el("quick-engine-install");
    if (button) {
      button.disabled = true;
      button.textContent = "正在启动…";
    }
    try {
      const data = await apiCall("/api/components/download", {
        method: "POST",
        body: JSON.stringify({ id: "gpt-sovits", source: "auto" }),
      });
      toastMessage("开始下载 GPT-SoVITS，可随时取消");
      pollEngineJob(data.job);
    } catch (error) {
      if (button) {
        button.disabled = false;
        button.textContent = "一键下载并安装";
      }
      toastMessage(error.message, "bad");
      if (error.payload?.job) pollEngineJob(error.payload.job);
    }
  }

  async function cancelEngine() {
    try {
      const data = await apiCall("/api/components/cancel", { method: "POST", body: "{}" });
      if (data?.job) renderEngineJob(data.job);
      toastMessage("正在取消下载");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function loadBankCounts() {
    try {
      const data = await apiCall("/api/localized/banks");
      const map = {};
      for (const bank of data.banks || []) {
        map[bank.id] = bank.stream_count || bank.file_count || 0;
      }
      state.bankCounts = map;
    } catch {
      state.bankCounts = {};
    }
    renderStep3();
  }

  function renderProjects() {
    const select = el("quick-project-select");
    if (!select) return;
    if (!state.projects.length) {
      select.innerHTML = '<option value="">还没有音色项目</option>';
      return;
    }
    select.innerHTML = state.projects
      .map((project) => {
        const model = project.model || {};
        const suffix = project.quick?.banks?.length
          ? " · 语音包"
          : model.mode === "fine_tuned" && model.state === "ready"
            ? " · 已训练"
            : " · 零样本";
        return `<option value="${escape(project.id)}">${escape(project.name)} · ${project.reference_count || 0} 段${suffix}</option>`;
      })
      .join("");
    if (state.project && state.projects.some((item) => item.id === state.project.id)) {
      select.value = state.project.id;
    }
  }

  function resetPack() {
    state.pack = null;
    state.prepare = null;
    state.cockpitSelection = null;
    state.preparedSignature = "";
    state.lines = [];
    state.categories = [];
    state.filter = "key";
    state.search = "";
    state.limit = 60;
    state.actionBusy = false;
    state.downloadSignature = "";
    state.exportInfo = null;
    state.installInfo = null;
    stopPlayback("", { reset: true });
    if (el("quick-custom")) el("quick-custom").hidden = true;
    if (el("quick-audition")) el("quick-audition").hidden = true;
    if (el("quick-download")) {
      el("quick-download").hidden = true;
      el("quick-download").innerHTML = "";
    }
    if (el("quick-pack-summary")) {
      el("quick-pack-summary").innerHTML =
        '<p class="muted small">选好国家和模式后点“准备语音包”，这里会列出每句的用途、文本和原音。</p>';
    }
    if (el("quick-dirty-note")) el("quick-dirty-note").hidden = true;
    setPill(el("quick-pack-pill"), "等待准备", "subtle");
    if (el("quick-generate-btn")) {
      el("quick-generate-btn").disabled = true;
      el("quick-generate-btn").textContent = "一键合成并打包";
    }
    state.previewUrl = "";
    const audio = el("quick-preview-audio");
    if (audio) {
      audio.removeAttribute("src");
      audio.load?.();
    }
    if (el("quick-preview-label")) el("quick-preview-label").textContent = "还没有试听音频";
  }

  function syncVehicleTheme() {
    const air = Boolean(el("quick-scope-air")?.checked);
    const ground = Boolean(el("quick-scope-ground")?.checked);
    const naval = Boolean(el("quick-scope-naval")?.checked);
    document.body.dataset.vehicle =
      air && !ground && !naval
        ? "air"
        : ground && !air && !naval
          ? "ground"
          : "both";
    renderCoach();
  }

  function renderCoach(stepId) {
    const step = stepId || state.quickStep || "quick-step-1";
    const tag = el("quick-coach-tag");
    if (tag) {
      const groups = [];
      if (el("quick-scope-air")?.checked) groups.push("空战组");
      if (el("quick-scope-ground")?.checked) groups.push("陆战组");
      if (el("quick-scope-naval")?.checked) groups.push("海战组");
      tag.textContent = `${groups.length ? groups.join(" + ") : "空战组 + 陆战组"} · 通讯员`;
    }
    const copy = COACH_COPY[step] || COACH_COPY["quick-step-1"];
    if (el("quick-coach-title")) el("quick-coach-title").textContent = copy.title;
    if (el("quick-coach-text")) el("quick-coach-text").textContent = copy.text;
  }

  function selectionSignature() {
    if (cockpitOn()) ensureCockpitSelection();
    return JSON.stringify({
      mode: state.mode,
      scopes: [...selectedBankScopes()].sort(),
      lineScopes: [...selectedLineScopes()].sort(),
      nations: [...state.selectedNations].sort(),
      banks: [...state.selectedBanks].sort(),
      cockpit: cockpitOn()
        ? [...(state.cockpitSelection instanceof Set ? state.cockpitSelection : [])].sort()
        : [],
    });
  }

  function renderDirtyNote() {
    const note = el("quick-dirty-note");
    if (!note) return;
    const dirty = Boolean(
      state.pack &&
        state.preparedSignature &&
        selectionSignature() !== state.preparedSignature,
    );
    note.hidden = !dirty;
    if (dirty) {
      note.innerHTML =
        "<strong>范围已改变</strong> 下面的台词表和试听还是上次准备的那一包；" +
        "点“准备语音包”会按新范围重新生成，旧包仍保留在项目里。";
    }
  }

  function renderRangeSummary() {
    const summary = el("quick-range-summary");
    if (!summary) return;
    const names = [];
    if (el("quick-scope-common")?.checked) names.push("通用");
    if (el("quick-scope-air")?.checked) names.push("空战");
    if (el("quick-scope-other")?.checked) names.push("其他");
    if (el("quick-scope-ground")?.checked) names.push("陆战");
    if (el("quick-scope-naval")?.checked) names.push("海战");
    if (el("quick-scope-cockpit")?.checked) names.push("座舱告警");
    const mode = state.mode === "original" ? "原语言换声" : "中文配音";
    const cockpitCount = cockpitOn() ? cockpitVoiceCount() : 0;
    summary.textContent =
      `声音范围：${names.length ? names.join(" + ") : "未选择"} · ${mode}` +
      (cockpitCount ? ` · 座舱告警 ${cockpitCount} 种` : "");
  }

  function renderMaterialList(project) {
    const box = el("quick-ref-list");
    if (!box) return;
    const refs = project?.reference?.files || [];
    const promptFile = project?.reference?.prompt_file || "";
    box.innerHTML = refs.length
      ? refs
          .map(
            (name) =>
              `<div class="ref-item${name === promptFile ? " primary" : ""}">` +
              `<button class="ref-play" type="button" data-ref-play="${escape(name)}" title="试听">▶</button>` +
              `<span class="ref-name" title="${escape(name)}">${escape(name)}</span>` +
              (name === promptFile ? '<span class="pill subtle">参考</span>' : "") +
              `<button class="ref-del" type="button" data-ref-del="${escape(name)}" title="删除">删除</button>` +
              `</div>`,
          )
          .join("")
      : '<p class="hint small">还没有素材。导入文件夹或添加音频文件，支持 wav / mp3 / flac / m4a / ogg。</p>';
  }

  function renderApply() {
    const pill = el("quick-apply-pill");
    const box = el("quick-apply-summary");
    if (!pill || !box) return;
    const gameReady = Boolean(state.options?.game_ready);
    const built = Boolean(state.pack?.export?.name);
    const ready = state.lines.filter((line) => line.audio).length;
    const total = state.lines.length;
    setPill(pill, built ? "已构建" : state.pack ? "待构建" : "待准备", built ? "ok" : "subtle");
    box.innerHTML =
      `<p class="hint small">${state.pack ? `台词 ${total} 句，已生成 ${ready} 句` : "还没有准备台词，先在第 3 步选择声音范围。"}` +
      (built ? ` · 已构建 ${Number(state.pack?.export?.lines || 0)} 句` : "") +
      `</p>` +
      `<p class="hint small">游戏目录：${gameReady ? "已选择，可写入 sound/mod" : "未选择，先到工作台选择战雷目录"}</p>`;
    const button = el("quick-apply-btn");
    if (button) {
      button.disabled = !state.pack || state.busy || state.actionBusy;
      button.textContent = built && state.installInfo ? "重新应用" : "构建并应用到游戏";
    }
  }

  function renderStep1() {
    const project = state.project;
    const refs = project?.reference?.files || [];
    const model = project?.model || {};
    const trained = model.mode === "fine_tuned" && model.state === "ready";
    const promptNode = el("quick-prompt");
    if (promptNode && promptNode.dataset.project !== (project?.id || "")) {
      promptNode.value = project?.reference?.prompt_text || "";
      promptNode.dataset.project = project?.id || "";
    }
    const promptText = promptNode?.value || "";
    setPill(
      el("quick-voice-pill"),
      !project ? "未选择" : trained ? "已训练" : refs.length ? "零样本可用" : "缺参考音频",
      trained ? "ok" : refs.length ? "subtle" : "warn",
    );
    renderMaterialList(project);
    setPill(
      el("quick-train-pill"),
      !project
        ? "等待素材"
        : trained
          ? "已训练"
          : refs.length >= 3
            ? "可训练"
            : refs.length
              ? "零样本可用"
              : "等待素材",
      trained ? "ok" : refs.length ? "subtle" : "warn",
    );
    setPill(
      el("quick-ref-pill"),
      refs.length ? `${refs.length} 段参考音频` : "没有参考音频",
      refs.length ? "ok" : "warn",
    );
    const hint = el("quick-model-hint");
    if (hint) {
      hint.textContent = !project
        ? "先选择或新建一个音色项目"
        : trained
          ? `${model.detail || "训练完成"}${model.seconds && Number(model.seconds) < 60 ? " · 素材偏少，补到 1 分钟以上更稳" : ""}${model.trained_at ? ` · ${new Date(model.trained_at).toLocaleString("zh-CN", { hour12: false })}` : ""}`
          : refs.length < 3
            ? `至少需要 3 段参考音频才能训练，当前 ${refs.length} 段；也可以先用零样本试听`
            : "还没有训练，先用参考音频做零样本试听也可以";
    }
    const train = el("quick-train-btn");
    if (train) {
      train.disabled = !project || refs.length < 3;
      train.textContent = trained ? "重新训练" : "训练专属音色";
    }
    if (el("quick-import-model-btn")) {
      el("quick-import-model-btn").disabled = !project || state.busy;
    }
    if (el("quick-import-btn")) el("quick-import-btn").disabled = !project;
    if (el("quick-detect-btn")) el("quick-detect-btn").disabled = !project || !refs.length;
    if (el("quick-preview-btn")) {
      el("quick-preview-btn").disabled = !project || !refs.length || !promptText.trim();
    }
    setPill(
      el("quick-preview-pill"),
      project && refs.length && promptText.trim()
        ? trained ? "已训练，可试听" : "零样本，可试听"
        : "等待音色",
      project && refs.length && promptText.trim() ? "ok" : "subtle",
    );
    el("quick-step-1")?.classList.toggle("done", Boolean(project && refs.length));
    el("quick-step-2")?.classList.toggle("active", Boolean(project && refs.length));
    renderStep3();
  }

  async function selectProject(projectId) {
    if (!projectId) {
      state.project = null;
      renderStep1();
      return;
    }
    const data = await apiCall(`/api/voice/project?project=${encodeURIComponent(projectId)}`);
    const changed = state.project?.id !== data.project.id;
    state.project = data.project;
    localStorage.setItem("wt.quick.project", projectId);
    el("quick-project-select").value = projectId;
    const isPack = Boolean(data.project.quick?.banks?.length && data.project.lines?.length);
    if (isPack) {
      restorePack(data.project);
    } else if (changed || state.pack) {
      resetPack();
    }
    renderStep1();
  }

  function restoreScope(project) {
    const quick = project.quick || {};
    const lineScopes = Array.isArray(quick.line_scopes) && quick.line_scopes.length
      ? new Set(quick.line_scopes)
      : quick.scopes?.includes("common")
        ? new Set(["common", "air", "other", ...(quick.scopes || [])])
        : new Set(quick.scopes || []);
    if (lineScopes.size) {
      for (const scope of ["common", "air", "other", "ground", "naval", "cockpit"]) {
        const node = el(`quick-scope-${scope}`);
        if (node) node.checked = lineScopes.has(scope);
      }
    }
    const known = new Set((state.options?.nations || []).map((nation) => nation.id));
    state.selectedNations.clear();
    for (const id of quick.nations || []) {
      if (known.has(id)) state.selectedNations.add(id);
    }
    const covered = new Set();
    for (const nation of state.options?.nations || []) {
      if (!state.selectedNations.has(nation.id)) continue;
      for (const bank of nation.banks || []) covered.add(bank.bank);
    }
    const languages = new Set(
      (state.options?.languages || []).map((bank) => bank.bank),
    );
    state.selectedBanks.clear();
    for (const bank of quick.banks || []) {
      if (bank === "aircraft_gui" || covered.has(bank)) continue;
      if (languages.has(bank)) state.selectedBanks.add(bank);
    }
  }

  function restorePack(project) {
    state.pack = project;
    state.cockpitSelection = null;
    state.lines = project.lines || [];
    if (project.quick?.mode) state.mode = project.quick.mode;
    restoreScope(project);
    const counts = new Map();
    for (const line of state.lines) {
      const bank = String(line.source?.bank || "");
      if (bank) counts.set(bank, (counts.get(bank) || 0) + 1);
    }
    const labels = new Map();
    for (const nation of state.options?.nations || []) {
      for (const bank of nation.banks || []) labels.set(bank.bank, bank.label);
    }
    if ((project.quick?.banks || []).includes("aircraft_gui")) {
      labels.set("aircraft_gui", cockpitInfo()?.label || "座舱告警音效");
    }
    state.prepare = {
      text_counts: project.quick?.text_counts || {},
      banks: [...counts.entries()].map(([bank, lines]) => ({
        bank,
        lines,
        label: labels.get(bank) || bank,
      })),
      missing_total: state.lines.filter(isMissingText).length,
    };
    state.filter = "key";
    state.search = "";
    state.limit = 60;
    state.preparedSignature = selectionSignature();
    renderPack();
    if (project.export?.name) downloadLinks();
  }

  async function loadProjects(preferred = "") {
    const data = await apiCall("/api/voice/projects");
    state.projects = data.projects || [];
    renderProjects();
    const stored = localStorage.getItem("wt.quick.project") || "";
    const trained = state.projects.find(
      (item) => item.model?.mode === "fine_tuned" && item.model?.state === "ready",
    );
    const target =
      (preferred && state.projects.some((item) => item.id === preferred) ? preferred : "") ||
      (state.project && state.projects.some((item) => item.id === state.project.id) ? state.project.id : "") ||
      (stored && state.projects.some((item) => item.id === stored) ? stored : "") ||
      trained?.id ||
      state.projects[0]?.id ||
      "";
    if (target) await selectProject(target);
    else {
      state.project = null;
      renderStep1();
    }
  }

  async function createProject() {
    const name = el("quick-new-name")?.value.trim();
    if (!name) {
      toastMessage("先给音色起个名字", "bad");
      return;
    }
    try {
      const data = await apiCall("/api/voice/projects", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      el("quick-new-name").value = "";
      await loadProjects(data.project.id);
      toastMessage("音色项目已创建，接着导入参考音频");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function importReference() {
    if (!state.project) return;
    try {
      const chosen = await apiCall("/api/dialog/folder", {
        method: "POST",
        body: JSON.stringify({ title: "选择参考音频文件夹" }),
      });
      if (chosen.cancelled || !chosen.path) return;
      el("quick-ref-status").textContent = "正在添加参考音频…";
      const data = await apiCall("/api/voice/reference/add", {
        method: "POST",
        body: JSON.stringify({
          project: state.project.id,
          paths: [chosen.path],
        }),
      });
      state.project = data.project;
      renderStep1();
      el("quick-ref-status").textContent = `已添加 ${data.count} 段参考音频`;
      if (!(state.project.reference?.prompt_text || "").trim()) {
        await detectPrompt(true);
      }
      toastMessage(`已添加 ${data.count} 段参考音频`);
    } catch (error) {
      el("quick-ref-status").textContent = error.message;
      toastMessage(error.message, "bad");
    }
  }

  async function addMaterialFiles() {
    if (!state.project) {
      toastMessage("先选择或新建音色项目", "bad");
      return;
    }
    try {
      const chosen = await apiCall("/api/dialog/files", {
        method: "POST",
        body: JSON.stringify({ title: "选择参考音频" }),
      });
      const paths = chosen.paths || [];
      if (!paths.length) return;
      const data = await apiCall("/api/voice/reference/add", {
        method: "POST",
        body: JSON.stringify({ project: state.project.id, paths }),
      });
      state.project = data.project;
      renderStep1();
      if (!(state.project.reference?.prompt_text || "").trim()) {
        await detectPrompt(true);
      }
      toastMessage(`已添加 ${data.count} 段素材`);
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function cleanMaterials() {
    if (!state.project) {
      toastMessage("先选择音色项目", "bad");
      return;
    }
    const trim = Boolean(el("quick-clean-trim")?.checked);
    const split = Boolean(el("quick-clean-split")?.checked);
    if (!trim && !split) {
      toastMessage("至少勾选一项整理", "bad");
      return;
    }
    const status = el("quick-clean-status");
    if (status) status.textContent = "正在整理素材…";
    try {
      const data = await apiCall("/api/voice/reference/process", {
        method: "POST",
        body: JSON.stringify({
          project: state.project.id,
          trim,
          split,
          max_seconds: Number(el("quick-clean-max")?.value || 30),
        }),
      });
      state.project = data.project;
      renderStep1();
      const summary = data.summary || {};
      const text =
        `完成：静音裁剪 ${summary.trimmed || 0} 个，切片 ${summary.split || 0} 个，` +
        `时长 ${summary.before_sec || 0}s → ${summary.after_sec || 0}s`;
      if (status) {
        status.textContent =
          text + (summary.warnings?.length ? ` · ${summary.warnings[0]}` : "");
      }
      toastMessage("素材整理完成");
    } catch (error) {
      if (status) status.textContent = error.message;
      toastMessage(error.message, "bad");
    }
  }

  async function applyToGame() {
    if (!state.pack) {
      toastMessage("先在第三步准备台词并生成语音", "bad");
      return;
    }
    if (!state.pack?.export?.name) {
      await generate();
    }
    if (!state.pack?.export?.name) {
      toastMessage("构建没有完成，请查看进度提示", "bad");
      return;
    }
    await installPackage();
    renderApply();
  }

  async function restoreInstall() {
    if (!confirm("把 sound/mod 恢复成安装前的备份吗？请先关闭游戏。")) return;
    try {
      const data = await apiCall("/api/packs/installed");
      const packs = data.packs || [];
      const banks = state.pack?.export?.banks || [];
      const target =
        state.installInfo?.pack ||
        packs.find((pack) =>
          (pack.banks || []).some((bank) => banks.includes(bank.name)),
        ) ||
        packs[0];
      if (!target?.id) {
        toastMessage("没有找到可恢复的安装记录", "bad");
        return;
      }
      await apiCall("/api/packs/uninstall", {
        method: "POST",
        body: JSON.stringify({ pack_id: target.id }),
      });
      state.installInfo = null;
      downloadLinks();
      renderApply();
      toastMessage("已恢复安装前状态");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function detectPrompt(silent = false) {
    if (!state.project) return;
    const button = el("quick-detect-btn");
    button.disabled = true;
    button.textContent = "识别中…";
    el("quick-ref-status").textContent = "正在用 FunASR 识别参考音频…";
    try {
      const data = await apiCall("/api/voice/transcribe", {
        method: "POST",
        body: JSON.stringify({
          project: state.project.id,
          file: state.project.reference?.prompt_file || "",
          lang: "zh",
        }),
      });
      state.project = data.project;
      const promptNode = el("quick-prompt");
      if (promptNode) {
        promptNode.value = data.project.reference?.prompt_text || data.text || "";
        promptNode.dataset.project = data.project.id;
      }
      renderStep1();
      el("quick-ref-status").textContent = `识别结果：${data.text}`;
      if (!silent) toastMessage("已识别参考文字，请核对后训练");
    } catch (error) {
      el("quick-ref-status").textContent = error.message;
      if (!silent) toastMessage(error.message, "bad");
    } finally {
      button.disabled = false;
      button.textContent = "自动识别参考文字";
    }
  }

  async function savePrompt() {
    if (!state.project) return;
    const text = el("quick-prompt").value.trim();
    if (!text) return;
    try {
      const data = await apiCall("/api/voice/prompt", {
        method: "POST",
        body: JSON.stringify({
          project: state.project.id,
          prompt_text: text,
          prompt_lang: "zh",
          prompt_file: state.project.reference?.prompt_file || "",
        }),
      });
      state.project = data.project;
      const promptNode = el("quick-prompt");
      if (promptNode) {
        promptNode.value = text;
        promptNode.dataset.project = state.project.id;
      }
      renderStep1();
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function trainModel() {
    if (!state.project) return;
    const refs = state.project.reference?.files || [];
    if (refs.length < 3) {
      toastMessage("至少需要 3 段参考音频", "bad");
      return;
    }
    if (!el("quick-prompt").value.trim()) {
      toastMessage("先填写或识别参考音频里的文字", "bad");
      el("quick-prompt").focus();
      return;
    }
    await savePrompt();
    if (!confirm("训练会用这些参考音频微调音色，并清空这个项目已有的合成音频。继续？")) return;
    state.busy = true;
    el("quick-train-btn").disabled = true;
    progress("quick-train", { message: "准备训练", percent: 0 });
    try {
      await startJob(
        "/api/voice/train",
        { project: state.project.id },
        (job) => progress("quick-train", job),
      );
      await selectProject(state.project.id);
      resetPack();
      toastMessage("训练完成，可以试听成果");
    } catch (error) {
      toastMessage(error.message, "bad");
      progress("quick-train", null);
    } finally {
      state.busy = false;
      renderStep1();
    }
  }

  async function trainRvc() {
    if (!state.project || state.busy) return;
    if (!confirm("用这个项目现有的训练素材训练一个换声模型，约 10-30 分钟。继续？")) return;
    state.busy = true;
    renderStep3();
    try {
      await startJob("/api/rvc/train", {
        project: state.project.id,
        epochs: 40,
      });
      await selectProject(state.project.id);
      toastMessage("换声模型训练完成，可以准备原语言包");
    } catch (error) {
      toastMessage(error.message, "bad");
    } finally {
      state.busy = false;
      renderStep3();
    }
  }

  async function preview() {
    if (!state.project) return;
    const text = el("quick-preview-text").value.trim();
    if (!text) {
      toastMessage("先输入要试听的文本", "bad");
      return;
    }
    await savePrompt();
    state.busy = true;
    el("quick-preview-btn").disabled = true;
    progress("quick-preview", { message: "准备试听", percent: 0 }, "quick-preview-text-status");
    try {
      const job = await startJob(
        "/api/voice/preview",
        { project: state.project.id, text },
        (item) => progress("quick-preview", item, "quick-preview-text-status"),
      );
      const url = job.result?.url || "";
      state.previewUrl = url;
      const audio = el("quick-preview-audio");
      audio.src = url;
      el("quick-preview-label").textContent =
        `试听：${text.slice(0, 32)}${text.length > 32 ? "…" : ""} · ${Number(job.result?.duration_sec || 0).toFixed(2)} 秒`;
      audio.play().catch(() => {});
      toastMessage("试听已生成");
    } catch (error) {
      toastMessage(error.message, "bad");
      el("quick-preview-label").textContent = error.message;
    } finally {
      state.busy = false;
      el("quick-preview-btn").disabled = false;
      progress("quick-preview", null);
    }
  }

  function renderModes() {
    const wrap = el("quick-modes");
    if (!wrap) return;
    const modes = state.options?.modes || [];
    wrap.innerHTML = modes
      .map((mode) => {
        const checked = state.mode === mode.id;
        return (
          `<label class="mode-option${checked ? " selected" : ""}" data-mode="${escape(mode.id)}">` +
          `<input type="radio" name="quick-mode" value="${escape(mode.id)}" ${checked ? "checked" : ""}>` +
          `<span><strong>${escape(mode.label)}</strong></span>` +
          `</label>`
        );
      })
      .join("");
    const note = el("quick-mode-note");
    if (note) {
      note.textContent = state.mode === "original"
        ? "保留原语言，只换音色；不翻译，也不朗读中文。"
        : "外语台词翻译成中文再合成；中文表直接用原文。";
    }
    const prepare = el("quick-prepare-btn");
    if (prepare) {
      prepare.textContent = state.mode === "original" ? "准备原语言包" : "准备中文配音包";
    }
    const step4sub = el("quick-step-4-sub");
    if (step4sub) {
      step4sub.textContent = state.mode === "original"
        ? "默认每句 1 版。先批量换声再试听，有问题单句重换，最后构建原语言包。"
        : "默认每句 1 版。先逐句试听，有问题就重生成，最后合成并打包。";
    }
    renderRvc();
  }

  function rvcReady() {
    return Boolean(state.project?.rvc?.model);
  }

  function projectHasAudio() {
    return Boolean(
      (state.project?.lines || []).some((line) => line.audio) ||
      (state.pack?.lines || []).some((line) => line.audio),
    );
  }

  async function pickModelFiles(title) {
    const data = await apiCall("/api/dialog/files", {
      method: "POST",
      body: JSON.stringify({ title, kind: "model" }),
    });
    return data.cancelled ? [] : data.paths || [];
  }

  async function importModelWeights() {
    if (!state.project) return;
    let paths;
    try {
      paths = await pickModelFiles("选择 GPT-SoVITS 权重：SoVITS 的 .pth 和 GPT 的 .ckpt");
    } catch (error) {
      toastMessage(error.message, "bad");
      return;
    }
    const pth = paths.filter((path) => /\.pth$/i.test(path));
    const ckpt = paths.filter((path) => /\.ckpt$/i.test(path));
    const sovits = pth[0] || "";
    const gpt = ckpt[0] || pth[1] || "";
    if (!sovits || !gpt) {
      if (paths.length) {
        toastMessage("要同时选 SoVITS 的 .pth 和 GPT 的 .ckpt 两个文件", "bad");
      }
      return;
    }
    if (
      projectHasAudio() &&
      !confirm("导入新模型会清空当前项目已生成的音频（参考素材保留）。继续？")
    ) {
      return;
    }
    try {
      const data = await apiCall("/api/voice/model/import", {
        method: "POST",
        body: JSON.stringify({
          project: state.project.id,
          sovits,
          gpt,
          reset_audio: true,
        }),
      });
      state.project = data.project;
      renderStep1();
      renderStep3();
      toastMessage("已导入 GPT-SoVITS 权重；重新准备语音包后会使用新音色");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function importRvcModel() {
    if (!state.project) return;
    let paths;
    try {
      paths = await pickModelFiles("选择 RVC 换声模型：.pth（可选同名 .index）");
    } catch (error) {
      toastMessage(error.message, "bad");
      return;
    }
    const pth = paths.filter((path) => /\.pth$/i.test(path));
    const index = paths.find((path) => /\.index$/i.test(path)) || "";
    if (!pth.length) {
      if (paths.length) toastMessage("没有选中 .pth 换声模型", "bad");
      return;
    }
    if (
      projectHasAudio() &&
      !confirm("导入新换声模型会清空当前项目已生成的音频（参考素材保留）。继续？")
    ) {
      return;
    }
    try {
      const data = await apiCall("/api/voice/rvc/model/import", {
        method: "POST",
        body: JSON.stringify({
          project: state.project.id,
          model: pth[0],
          index,
          reset_audio: true,
        }),
      });
      state.project = data.project;
      renderStep3();
      toastMessage(
        index
          ? "已导入换声模型和索引；重新准备原语言包后生效"
          : "已导入换声模型（没有索引，音色相似度可能略低）；重新准备后生效",
      );
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  function renderRvc() {
    const wrap = el("quick-rvc");
    const status = el("quick-rvc-status");
    const button = el("quick-rvc-train");
    if (!wrap || !status || !button) return;
    const original = state.mode === "original";
    wrap.hidden = !original;
    if (!original) return;
    const runtimeReady = Boolean(state.options?.rvc?.available);
    const importButton = el("quick-rvc-import");
    if (importButton) {
      importButton.hidden = false;
      importButton.disabled = state.busy || !runtimeReady;
    }
    if (!runtimeReady) {
      status.textContent = "还没有安装 RVC 换声运行时（约 5-8GB，会按显卡自动选择版本）；安装后才能训练或导入换声模型。";
      button.hidden = false;
      button.disabled = state.busy;
      button.textContent = "下载并安装 RVC";
      return;
    }
    const rvc = state.project?.rvc || {};
    if (rvcReady()) {
      const seconds = Number(rvc.seconds || 0);
      status.textContent = `换声模型已就绪${rvc.segments ? ` · ${rvc.segments} 段` : ""}${seconds ? ` · 训练 ${Math.round(seconds / 60)} 分钟` : ""}`;
      button.hidden = true;
    } else {
      status.textContent = "还没有换声模型；用同一套训练素材训练一次，约 10-30 分钟。";
      button.hidden = false;
      button.disabled = state.busy;
      button.textContent = "训练换声模型";
    }
  }

  async function installRvc() {
    if (state.busy) return;
    const component = state.options?.rvc_component || "rvc-nvidia";
    if (!confirm("下载 RVC 换声运行时约 5-8GB，解压后约 10-16GB。继续？")) return;
    state.busy = true;
    renderStep3();
    const status = el("quick-rvc-status");
    try {
      const data = await apiCall("/api/components/download", {
        method: "POST",
        body: JSON.stringify({ id: component, source: "auto" }),
      });
      while (data?.job) {
        const job = data.job;
        if (status) {
          status.textContent = `${job.state === "extracting" ? "解压" : "下载"} RVC 运行时：${job.message || ""}${job.percent != null ? ` · ${Number(job.percent).toFixed(0)}%` : ""}`;
        }
        if (!job.active) {
          if (job.state === "done") {
            toastMessage("RVC 换声运行时已安装");
            await loadOptions();
            if (state.project) await selectProject(state.project.id);
          } else if (job.state === "error") {
            throw new Error(job.error || job.message || "RVC 安装失败");
          }
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const next = await apiCall("/api/components/job");
        data.job = next.job;
      }
    } catch (error) {
      toastMessage(error.message, "bad");
    } finally {
      state.busy = false;
      renderStep3();
    }
  }

  async function rvcAction() {
    if (!state.options?.rvc?.available) {
      await installRvc();
    } else {
      await trainRvc();
    }
  }

  function languageGroups(scopes = selectedBankScopes()) {
    const wanted = new Set(scopes);
    const groups = new Map();
    for (const bank of state.options?.languages || []) {
      if (!wanted.has(bank.scope)) continue;
      const id = bank.language || bank.bank;
      if (!groups.has(id)) {
        groups.set(id, {
          id,
          label: bank.language_label || id,
          banks: [],
          scopes: new Set(),
        });
      }
      const group = groups.get(id);
      group.banks.push(bank);
      group.scopes.add(bank.scope);
    }
    return [...groups.values()];
  }

  function effectiveBankIds(scopes = selectedBankScopes()) {
    const wanted = new Set(scopes);
    const byBank = new Map(
      (state.options?.languages || []).map((bank) => [bank.bank, bank]),
    );
    const result = new Set(
      [...state.selectedBanks].filter((bank) => {
        const item = byBank.get(bank);
        return item && wanted.has(item.scope);
      }),
    );
    for (const nation of state.options?.nations || []) {
      if (!state.selectedNations.has(nation.id)) continue;
      for (const bank of nation.banks || []) {
        if (bank.available && wanted.has(bank.scope)) result.add(bank.bank);
      }
    }
    return result;
  }

  function renderLanguageChips() {
    const wrap = el("quick-language-chips");
    if (!wrap) return;
    const groups = languageGroups();
    if (!groups.length) {
      wrap.innerHTML = "";
      return;
    }
    const effective = effectiveBankIds();
    wrap.innerHTML = groups
      .map((group) => {
        const active =
          group.banks.length > 0 &&
          group.banks.every((bank) => effective.has(bank.bank));
        const scopes = lineScopeLabel(group.banks) || "语音表";
        return (
          `<button class="chip${active ? " active" : ""}" type="button" data-language="${escape(group.id)}" title="${escape(scopes)}">` +
          `${escape(group.label)} · ${group.banks.length}` +
          `</button>`
        );
      })
      .join("");
  }

  function renderLanguageBanks() {
    const wrap = el("quick-language-banks");
    if (!wrap) return;
    const scopes = new Set(selectedBankScopes());
    if (!scopes.has("common") && !scopes.has("ground") && !scopes.has("naval")) {
      wrap.innerHTML = '<p class="muted small">当前只选了座舱告警；要乘员语音表，请勾选上方“通用/空战/其他”“陆战”或“海战”。</p>';
      return;
    }
    const banks = (state.options?.languages || []).filter((bank) => scopes.has(bank.scope));
    if (!banks.length) {
      wrap.innerHTML = '<p class="muted small">没有读取到游戏语音表；请先在工作台选择战雷目录。</p>';
      return;
    }
    wrap.innerHTML = banks
      .map((bank) => {
        const checked = state.selectedBanks.has(bank.bank);
        return (
          `<label class="bank-check${checked ? " selected" : ""}" data-bank="${escape(bank.bank)}">` +
          `<input type="checkbox" ${checked ? "checked" : ""}>` +
          `<span>${escape(bank.label)}</span>` +
          `${bank.supplemental ? '<span class="muted">补充</span>' : ""}` +
          `</label>`
        );
      })
      .join("");
  }

  function renderCockpitAudit() {
    const info = cockpitInfo();
    const summary = el("quick-cockpit-summary");
    const body = el("quick-cockpit-body");
    if (!summary || !body) return;
    if (!info) {
      summary.textContent = "座舱音效清单（读取中）";
      body.innerHTML = '<p class="hint small">正在读取 aircraft_gui…</p>';
      return;
    }
    if (!info.available) {
      summary.textContent = "座舱音效清单（不可用）";
      body.innerHTML =
        '<p class="hint small">没有在官方 FMOD 工程里找到 aircraft_gui / Assets/vws；请重新选择工程目录。</p>';
      return;
    }
    const missingEffects = (info.kit_missing_effects || []).length;
    const restoreKill =
      (info.kit_missing_effects || []).includes("ui_kill_assist") &&
      Boolean(state.options?.game_ready);
    const unresolved = Math.max(0, missingEffects - (restoreKill ? 1 : 0));
    const preserved = Math.max(
      0,
      info.preserved_streams - missingEffects + (restoreKill ? 1 : 0),
    );
    summary.textContent =
      `座舱音效清单：${info.voice_streams} 条 VWS 人声 / ${info.voice_names} 种台词可重配` +
      (info.extra_streams ? `，另有 ${info.extra_streams} 条炮手/击杀/助攻提示可选` : "") +
      `，${preserved} 条音效保留原音` +
      (unresolved ? `（另有 ${unresolved} 条官方工程未收录）` : "");
    const rows = (info.categories || [])
      .map((item) => {
        const samples = (item.samples || []).join("、");
        return (
          `<div class="cockpit-audit-row">` +
          `<span class="cockpit-audit-name">${escape(item.label)}</span>` +
          `<span class="cockpit-audit-count">${item.names} 种 · ${item.streams} 条</span>` +
          `<span class="cockpit-audit-sample mono">${escape(samples)}</span>` +
          `</div>`
        );
      })
      .join("");
    const families = (info.families || []).length
      ? `<p class="cockpit-families">原声三套：` +
        info.families
          .map((item) => `${escape(item.label)} ${item.files} 条`)
          .join(" · ") +
        `；安装后三套一起替换，中文模式会把它们都换成你的中文声线。</p>`
      : "";
    const extras = (info.extra_groups || [])
      .map(
        (item) =>
          `${escape(item.group)} ${item.streams} 条` +
          (item.default_selected ? "" : "（默认不勾选）"),
      )
      .join(" · ");
    const extraLine = extras
      ? `<p class="cockpit-families">可选重配：${extras}。勾选后在下一步会出现对应台词。</p>`
      : "";
    const missing = (info.kit_missing_effects || []).length
      ? `<p class="hint small">官方工程比当前游戏少 ${info.kit_missing_effects.length} 条音效：` +
        `${escape(info.kit_missing_effects.join("、"))}；` +
        (restoreKill
          ? `助攻提示音 ui_kill_assist 会从游戏原音自动补回，并出现在下方“击杀/助攻提示音”里，默认保留原音；`
          : "") +
        `其余 ${unresolved} 条以工程版本为准。</p>`
      : "";
    body.innerHTML =
      families +
      extraLine +
      rows +
      `<p class="hint small">${escape(info.note || "")}</p>` +
      missing;
  }

  function renderCockpitPicker() {
    const box = el("quick-cockpit-picker");
    const summary = el("quick-cockpit-picker-summary");
    const body = el("quick-cockpit-picker-body");
    const picked = el("quick-cockpit-picked");
    if (!box || !summary || !body) return;
    const info = cockpitInfo();
    if (!cockpitOn() || !info?.available) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    ensureCockpitSelection();
    const items = cockpitItems();
    const selected = selectedCockpitItems();
    const variants = selected.reduce(
      (total, item) => total + Number(item.variant_count || 0),
      0,
    );
    summary.textContent =
      `选择要重配的座舱告警 · 已选 ${selected.length} / ${items.length} 种` +
      (variants ? `（${variants} 条变体）` : "");
    if (picked) {
      picked.textContent = selected.length
        ? `将生成 ${selected.length} 句；重复语气变体一次覆盖`
        : "没有选中任何一条，准备按钮会保持不可用";
    }
    if (!items.length) {
      body.innerHTML = '<p class="hint small">没有可选的座舱告警。</p>';
      return;
    }
    const order = [
      "目标与方位",
      "雷达与导弹",
      "飞行与起降",
      "动力与系统",
      "燃油与对抗",
      "其他告警",
      "炮手语音",
      "击杀/助攻提示音（原为音效）",
    ];
    const grouped = new Map();
    for (const item of items) {
      const group = item.group || "其他告警";
      if (!grouped.has(group)) grouped.set(group, []);
      grouped.get(group).push(item);
    }
    const groups = [...grouped.entries()].sort(
      (a, b) =>
        (order.indexOf(a[0]) < 0 ? 99 : order.indexOf(a[0])) -
        (order.indexOf(b[0]) < 0 ? 99 : order.indexOf(b[0])),
    );
    body.innerHTML = groups
      .map(([group, list]) => {
        const rows = list
          .sort((a, b) => String(a.label).localeCompare(String(b.label), "zh-CN"))
          .map((item) => {
            const checked = state.cockpitSelection.has(item.event_id);
            const tag =
              item.kind === "effect"
                ? '<span class="cockpit-kind">原为音效</span>'
                : item.kind === "voice"
                  ? '<span class="cockpit-kind">炮手语音</span>'
                  : "";
            const note =
              item.kind === "vws"
                ? ""
                : `<span class="cockpit-pick-note">${escape(item.note || "")}</span>`;
            const pickLabel = String(item.label || item.event)
              .replace(/^座舱告警 · /, "")
              .replace("（原为音效）", "");
            return (
              `<label class="cockpit-pick-row">` +
              `<input type="checkbox" data-cockpit-event="${escape(item.event_id)}" ${checked ? "checked" : ""}>` +
              `<span class="cockpit-pick-label">${escape(pickLabel)}${tag}${note}</span>` +
              `<span class="cockpit-pick-meta">${item.variant_count > 1 ? `${item.variant_count} 条变体` : "1 条"}</span>` +
              `</label>`
            );
          })
          .join("");
        const count = list.filter((item) => state.cockpitSelection.has(item.event_id)).length;
        return (
          `<div class="cockpit-pick-group">` +
          `<div class="cockpit-pick-group-head"><span>${escape(group.replace("（原为音效）", ""))}</span>` +
          `<span>${count} / ${list.length}</span></div>` +
          rows +
          `</div>`
        );
      })
      .join("");
  }

  const NATION_CODES = {
    us: "US",
    de: "DE",
    ru: "SU",
    gb: "UK",
    uk: "UK",
    jp: "JP",
    cn: "CN",
    it: "IT",
    fr: "FR",
    se: "SE",
    il: "IL",
  };

  function renderStep3() {
    syncVehicleTheme();
    renderRangeSummary();
    renderDirtyNote();
    renderApply();
    const options = state.options;
    const nations = el("quick-nations");
    if (!nations) return;
    renderModes();
    renderCockpitAudit();
    renderCockpitPicker();
    const lineScopes = selectedLineScopes();
    const cockpitOnly =
      cockpitOn() &&
      Boolean(cockpitInfo()?.available) &&
      !lineScopes.some((scope) => scope !== "cockpit");
    const nationNotice = el("quick-nation-notice");
    if (nationNotice) nationNotice.hidden = !cockpitOnly;
    const nationHead = el("quick-nation-head");
    if (nationHead) nationHead.hidden = cockpitOnly;
    nations.hidden = cockpitOnly;
    const allBanks = el("quick-all-banks");
    if (allBanks) allBanks.hidden = cockpitOnly;
    if (!options?.nations?.length) {
      nations.innerHTML = '<p class="muted small">正在读取语音表…</p>';
      return;
    }
    nations.innerHTML = options.nations
      .map((nation) => {
        const checked = state.selectedNations.has(nation.id);
        const inScope = (nation.banks || []).filter(
          (bank) => selectedBankScopes().includes(bank.scope) && bank.available,
        );
        const unavailable = (nation.banks || []).filter(
          (bank) => selectedBankScopes().includes(bank.scope) && !bank.available,
        );
        const labels = lineScopeLabel(inScope);
        const notes = [
          nation.note,
          inScope.find((bank) => bank.note)?.note,
          unavailable[0]?.note,
        ].filter(Boolean);
        const note = [...new Set(notes)].join("；");
        return (
          `<label class="nation-card${checked ? " selected" : ""}${inScope.length ? "" : " disabled"}" data-nation="${escape(nation.id)}">` +
          `<input type="checkbox" ${checked ? "checked" : ""}${inScope.length ? "" : " disabled"}>` +
          `<span class="nation-code">${escape(NATION_CODES[nation.id] || nation.id.slice(0, 2).toUpperCase())}</span>` +
          `<div><strong>${escape(nation.label)} · ${escape(nation.language_label)}</strong>` +
          `<span class="muted">${escape(labels || "当前范围没有可用表")}${note ? `；${escape(note)}` : ""}</span></div>` +
          `</label>`
        );
      })
      .join("");
    renderLanguageBanks();
    renderLanguageChips();

    const commonButton = el("quick-banks-common");
    if (commonButton) {
      const line = new Set(selectedLineScopes());
      const labels = [];
      if (line.has("common")) labels.push("通用");
      if (line.has("air")) labels.push("空战");
      if (line.has("other")) labels.push("其他");
      commonButton.textContent = labels.length
        ? `全选${labels.join("/")}`
        : "全选 common 表台词";
      commonButton.disabled = !(
        line.has("common") || line.has("air") || line.has("other")
      );
    }
    const groundButton = el("quick-banks-ground");
    if (groundButton) {
      groundButton.disabled = !selectedLineScopes().includes("ground");
    }
    const navalButton = el("quick-banks-naval");
    if (navalButton) {
      navalButton.disabled = !selectedLineScopes().includes("naval");
    }

    const banks = availableBanks();
    const line = new Set(selectedLineScopes());
    const allCommonParts =
      line.has("common") && line.has("air") && line.has("other");
    const partialCommon = selectedBankScopes().includes("common") && !allCommonParts;
    const cockpitAvailable = cockpitOn() && cockpitInfo()?.available;
    const cockpitReady = cockpitAvailable && cockpitVoiceCount() > 0;
    const cockpitLines = cockpitReady ? cockpitVoiceCount() : 0;
    const cockpitVariants = cockpitReady ? cockpitVariantCount() : 0;
    const total = partialCommon ? null : totalBankLines(banks) + cockpitLines;
    const summary = el("quick-bank-summary");
    if (summary) {
      const chips = banks.map((bank) => {
        const count =
          partialCommon && bank.scope === "common"
            ? 0
            : state.bankCounts[bank.bank];
        return `<span class="chip">${escape(bank.label)}${count ? ` · ${count} 条` : ""}</span>`;
      });
      if (cockpitReady) {
        chips.push(
          `<span class="chip">座舱告警 · ${cockpitLines} 种 / ${cockpitVariants} 条变体</span>`,
        );
      }
      summary.innerHTML = chips.length
        ? chips.join("")
        : '<span class="muted small">还没有选择国家、语音表或座舱告警。</span>';
    }
    const selectionCount = banks.length + (cockpitReady ? 1 : 0);
    setPill(
      el("quick-select-pill"),
      selectionCount
        ? `${selectionCount} 项${total ? ` · 约 ${total} 条` : " · 条数准备时统计"}`
        : "未选择",
      selectionCount ? "ok" : "subtle",
    );
    const hint = el("quick-prepare-hint");
    if (hint) {
      const ready = (state.project?.reference?.files || []).length &&
        (el("quick-prompt")?.value || "").trim();
      const catalogReady = state.options?.catalog?.ready;
      if (!banks.length && cockpitOn() && cockpitInfo()?.available && !cockpitReady) {
        hint.textContent = "座舱已勾选，但还没选中任何告警；在“选择要重配的座舱告警”里至少勾一条。";
      } else if (!banks.length && !cockpitReady) {
        hint.textContent = "先选国家、勾选语音表或座舱告警。";
      } else if (!ready) {
        hint.textContent = "先在第一步导入参考音频并填写参考文字。";
      } else if (cockpitOnly) {
        hint.textContent =
          state.mode === "original"
            ? `座舱告警不区分国家。约 ${total || "?"} 句，保留原语言只换音色，首次需训练换声模型（约 10-30 分钟）。`
            : `座舱告警不区分国家。约 ${total || "?"} 句，使用内置中文文本，直接准备即可。`;
      } else if (state.mode === "original") {
        hint.textContent = `${banks.length} 张表${cockpitReady ? " + 座舱告警" : ""}；保留原语言原语气，只换音色，不需要听写或翻译。首次需要训练换声模型（约 10-30 分钟）。`;
      } else if (partialCommon) {
        const scopeLabels = { common: "通用", air: "空战", other: "其他", ground: "陆战", naval: "海战" };
        const picked = [...line].filter((scope) => scopeLabels[scope]).map((scope) => scopeLabels[scope]).join("/");
        hint.textContent = `${banks.length} 张表${cockpitReady ? " + 座舱告警" : ""}；已选 ${picked} 范围，具体句数在准备时统计。首次会先用 FunASR 听写官方中文原音（约 3-10 分钟）。`;
      } else if (!catalogReady && !selectedBankScopes().some((scope) => scope === "common" || scope === "ground")) {
        hint.textContent = `约 ${total || "?"} 句；海战使用内置首轮译法，座舱告警使用内置文本，都不需要听写。`;
      } else {
        hint.textContent = catalogReady
          ? `约 ${total || "?"} 句；预计合成 ${humanMinutes(total)}（相同文本会去重，实际更少）。${cockpitReady ? " 座舱只重配选中项，其余音效保留原音。" : ""}`
          : `约 ${total || "?"} 句；首次会先用 FunASR 听写官方中文原音（约 3-10 分钟），再填文本。`;
      }
    }
    const prepare = el("quick-prepare-btn");
    if (prepare) {
      const refs = state.project?.reference?.files || [];
      const prompt = el("quick-prompt")?.value || "";
      const modeBlocked = state.mode === "original" && !rvcReady();
      const hasTargets = Boolean(banks.length || cockpitReady);
      prepare.disabled = !state.project || !refs.length || !prompt.trim() || !hasTargets || state.busy || modeBlocked;
    }
    el("quick-step-3")?.classList.toggle("active", Boolean(state.project?.reference?.files?.length));
    syncPresetMark();
  }

  const PRESET_SCOPES = {
    air: ["quick-scope-common", "quick-scope-air", "quick-scope-other"],
    ground: ["quick-scope-common", "quick-scope-ground"],
    naval: ["quick-scope-common", "quick-scope-naval"],
    cockpit: ["quick-scope-cockpit"],
    all: [
      "quick-scope-common",
      "quick-scope-air",
      "quick-scope-other",
      "quick-scope-ground",
      "quick-scope-naval",
      "quick-scope-cockpit",
    ],
  };
  const PRESET_LABELS = {
    air: "空战语音包",
    ground: "陆战语音包",
    naval: "海战语音包",
    cockpit: "座舱告警包",
    all: "全部语音",
  };
  const SCOPE_IDS = PRESET_SCOPES.all;
  const CORE_SCOPE_IDS = SCOPE_IDS.filter((id) => id !== "quick-scope-common");

  function scopeOn(id) {
    return Boolean(el(id)?.checked);
  }

  function presetActive(kind) {
    const ids = PRESET_SCOPES[kind] || [];
    return ids.length > 0 && ids.every(scopeOn);
  }

  function markPreset() {
    for (const card of document.querySelectorAll("#quick-presets .preset-card")) {
      const active = presetActive(card.dataset.preset);
      card.classList.toggle("active", active);
      card.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  function syncPresetMark() {
    markPreset();
  }

  function applyPreset(kind) {
    const ids = PRESET_SCOPES[kind] || [];
    if (!ids.length) return;
    const setScope = (id, checked) => {
      const node = el(id);
      if (node) node.checked = checked;
    };
    const cockpitBefore = scopeOn("quick-scope-cockpit");
    let added;
    if (kind === "all") {
      added = !SCOPE_IDS.every(scopeOn);
      for (const id of SCOPE_IDS) setScope(id, added);
    } else {
      added = !presetActive(kind);
      if (added) {
        for (const id of ids) setScope(id, true);
      } else {
        for (const id of ids) {
          if (id !== "quick-scope-common") setScope(id, false);
        }
        const anyCore = CORE_SCOPE_IDS.some(scopeOn);
        if (kind !== "cockpit" && !anyCore) {
          setScope("quick-scope-common", false);
        }
      }
    }
    const lineScopes = selectedLineScopes();
    const scopes = selectedBankScopes();
    state.selectedNations.clear();
    const onlyCockpit = lineScopes.length === 1 && lineScopes[0] === "cockpit";
    const navalOnly =
      lineScopes.includes("naval") &&
      !lineScopes.some((scope) => !["common", "naval", "cockpit"].includes(scope));
    if (!onlyCockpit) {
      for (const nation of state.options?.nations || []) {
        const banks = nation.banks || [];
        const matched = navalOnly
          ? banks.some((bank) => bank.available && bank.scope === "naval")
          : banks.some((bank) => bank.available && scopes.includes(bank.scope));
        if (matched) state.selectedNations.add(nation.id);
      }
    }
    state.selectedBanks.clear();
    const cockpitAfter = scopeOn("quick-scope-cockpit");
    if (cockpitAfter && !cockpitBefore) {
      ensureCockpitSelection();
      state.cockpitSelection = new Set(
        cockpitItems()
          .filter((item) => item.default_selected)
          .map((item) => item.event_id),
      );
    }
    renderStep3();
    const range = el("quick-range");
    if (range) range.open = false;
    if (kind === "all") {
      toastMessage(
        added
          ? "已选中全部语音范围；再点一次“全部语音”可以清空"
          : "已清空全部语音范围",
      );
      return;
    }
    const label = PRESET_LABELS[kind] || "预设";
    const note = kind === "naval" && added ? "；海战首轮译法需听原音校对" : "";
    toastMessage(
      added
        ? `已加入${label}，可继续点其他卡片叠加${note}`
        : `已取消${label}`,
    );
  }

  function compareSource() {
    if (!state.pack) return;
    const line =
      state.lines.find((item) => item.id === playback.currentId) ||
      state.lines.find((item) => item.audio);
    if (!line) {
      toastMessage("先生成或导入一句音频", "bad");
      return;
    }
    if (playback.active) stopPlayback();
    const url =
      `/api/audio/source?project=${encodeURIComponent(state.pack.id)}` +
      `&line=${encodeURIComponent(line.id)}`;
    new Audio(url)
      .play()
      .catch(() => toastMessage("原音播放失败，可能这句没有游戏原音", "bad"));
    toastMessage(
      `原音对比：${String(line.source?.event || line.text || "当前句").slice(0, 24)}`,
    );
  }

  function toggleNation(nationId, checked) {
    if (checked) state.selectedNations.add(nationId);
    else state.selectedNations.delete(nationId);
    renderStep3();
  }

  function toggleBank(bankId, checked) {
    if (checked) state.selectedBanks.add(bankId);
    else state.selectedBanks.delete(bankId);
    renderStep3();
  }

  async function preparePack() {
    if (!state.project || !selectedLineScopes().length) return;
    const banks = availableBanks();
    const cockpitReady = cockpitOn() && cockpitInfo()?.available && cockpitVoiceCount() > 0;
    if (!banks.length && !cockpitReady) return;
    const names = (state.options?.nations || [])
      .filter((nation) => state.selectedNations.has(nation.id))
      .map((nation) => nation.label);
    const customNames = [...state.selectedBanks]
      .map((id) => (state.options?.languages || []).find((bank) => bank.bank === id)?.label)
      .filter(Boolean);
    if (cockpitReady) names.push("座舱告警");
    const label = [...names, ...customNames].join("/") || "自定义";
    const packName = `${state.project.name} · ${label} ${state.mode === "original" ? "原语言包" : "包"}`;
    const signature = selectionSignature();
    state.busy = true;
    el("quick-prepare-btn").disabled = true;
    progress("quick-prepare", { message: "准备语音包", percent: 0 });
    try {
      const job = await startJob(
        "/api/quick/prepare",
        {
          source_project: state.project.id,
          nations: [...state.selectedNations],
          banks: [...state.selectedBanks],
          scopes: selectedBankScopes(),
          line_scopes: selectedLineScopes(),
          cockpit_events: cockpitReady ? [...state.cockpitSelection] : [],
          mode: state.mode,
          name: packName,
        },
        (item) => progress("quick-prepare", item),
      );
      state.prepare = job.result || null;
      state.pack = job.result?.project || null;
      state.preparedSignature = signature;
      state.lines = state.pack?.lines || [];
      state.filter = "key";
      state.search = "";
      state.limit = 60;
      localStorage.setItem("wt.voice.project", state.pack?.id || "");
      localStorage.setItem("wt.quick.project", state.pack?.id || "");
      renderPack();
      toastMessage(`已准备 ${state.lines.length} 句，先核对重点文本`);
      el("quick-pack-summary")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      toastMessage(error.message, "bad");
      progress("quick-prepare", null);
    } finally {
      state.busy = false;
      if (state.pack) {
        renderPack();
      } else {
        renderStep3();
      }
    }
  }

  function syncGenerateButton() {
    const button = el("quick-generate-btn");
    if (!button) return;
    const built = Boolean(state.pack?.export?.name);
    if (isOriginalMode()) {
      const pending = state.lines.filter(needsConversion).length;
      button.disabled = !state.lines.length || state.busy || built;
      button.textContent = built
        ? "已打包，可导出/安装"
        : pending
          ? `批量换声并打包（${pending} 句）`
          : "直接构建原语言包";
      return;
    }
    const pending = state.lines.filter(needsSynthesis).length;
    button.disabled = !state.lines.length || state.busy || built;
    button.textContent = built
      ? "已打包，可导出/安装"
      : pending
        ? `生成缺失并打包（${pending} 句）`
        : "直接构建语音包";
  }

  function audioLines() {
    return state.lines.filter(
      (line) => line.audio && line.text_source !== "non_voice",
    );
  }

  function setAuditionStatus(text) {
    const node = el("quick-audition-status");
    if (node) node.textContent = text;
  }

  function audioIndex(lineId) {
    return audioLines().findIndex((line) => line.id === lineId);
  }

  function nextAudioId(lineId) {
    const lines = audioLines();
    if (!lines.length) return "";
    const index = lines.findIndex((line) => line.id === lineId);
    if (index < 0) return lines[0].id;
    return lines[(index + 1) % lines.length]?.id || lines[0].id;
  }

  function updateAuditionControls() {
    el("quick-audition")?.classList.toggle("playing", playback.active);
    const ready = audioLines().length;
    const play = el("quick-play-all");
    if (play) {
      play.disabled =
        !ready || state.busy || Boolean(state.lineBusy) || playback.active;
      play.textContent = ready ? `一键试听全部（${ready} 句）` : "一键试听全部";
    }
    const cont = el("quick-play-continue");
    if (cont) {
      const index = playback.resumeId ? audioIndex(playback.resumeId) : -1;
      cont.disabled =
        !ready ||
        index < 0 ||
        state.busy ||
        Boolean(state.lineBusy) ||
        playback.active;
      cont.title =
        index >= 0 ? `从第 ${index + 1} 句继续` : "试听或停止后，可从上次位置继续";
    }
    const only = el("quick-synth-only");
    if (only) {
      only.disabled = !state.lines.length || state.busy || Boolean(state.lineBusy);
      only.textContent = isOriginalMode() ? "批量换声（先试听）" : "先生成一版（先试听）";
    }
    const stop = el("quick-stop-all");
    if (stop) stop.disabled = !playback.active;
    const loop = el("quick-loop-audition");
    if (loop) loop.checked = playback.loop;
    const locate = el("quick-locate-current");
    if (locate) {
      const lineId = playback.currentId || playback.lastLine?.id || "";
      locate.disabled = !lineId || !state.lines.some((line) => line.id === lineId);
    }
  }

  function updateAudition() {
    const pod = el("quick-audition");
    if (!pod) return;
    pod.hidden = !state.lines.length;
    updateAuditionControls();
    if (!playback.active) {
      const ready = audioLines().length;
      if (isOriginalMode()) {
        setAuditionStatus(
          ready
            ? `已换声 ${ready}/${state.lines.length} 句；按列表顺序连续播放，保留原语言。`
            : "还没有换声；先点“批量换声（先试听）”或“一键换声并打包”。",
        );
      } else {
        setAuditionStatus(
          ready
            ? `已生成 ${ready}/${state.lines.length} 句；按列表顺序连续播放，不满意点该句“重新生成”。`
            : "还没有生成语音；先点“先生成一版（先试听）”，或直接“一键合成并打包”。",
        );
      }
    }
  }

  function clearPlayingMark() {
    document
      .querySelectorAll(".quick-line.playing")
      .forEach((node) => node.classList.remove("playing"));
  }

  function markPlaying(lineId) {
    clearPlayingMark();
    const card = document.querySelector(
      `.quick-line[data-line-id="${CSS.escape(lineId)}"]`,
    );
    if (!card) return;
    card.classList.add("playing");
    // 不自动滚动列表：连续试听时每次滚到下一句会让整个界面跟着滑动。
  }

  function resetNowPlaying() {
    setPill(el("quick-now-pill"), "当前试听", "subtle");
    el("quick-now-purpose").textContent = "未在播放";
    el("quick-now-text").textContent = "点“一键试听全部”后这里显示当前台词";
    el("quick-now-index").textContent = "";
  }

  function showNowPlaying(line, label = "正在播放") {
    const index = audioIndex(line.id);
    setPill(
      el("quick-now-pill"),
      label,
      label === "正在播放" ? "ok" : "subtle",
    );
    el("quick-now-purpose").textContent =
      line.source?.description || line.source?.event || "台词";
    el("quick-now-text").textContent =
      line.text_source === "original"
        ? " · 原语言换声"
        : line.text
          ? ` · ${line.text}`
          : "";
    el("quick-now-index").textContent =
      index >= 0 ? `第 ${index + 1}/${audioLines().length} 句` : "";
  }

  function locateCurrentLine() {
    const lineId = playback.currentId || playback.lastLine?.id || "";
    if (!lineId) return;
    let card = document.querySelector(
      `.quick-line[data-line-id="${CSS.escape(lineId)}"]`,
    );
    if (!card) {
      state.filter = "all";
      state.search = "";
      state.limit = Math.max(state.limit, state.lines.length);
      buildCategories();
      if (el("quick-search")) el("quick-search").value = "";
      renderLines();
      card = document.querySelector(
        `.quick-line[data-line-id="${CSS.escape(lineId)}"]`,
      );
    }
    if (!card) {
      toastMessage("这句在当前筛选/分页里找不到，请点“全部”后再试", "bad");
      return;
    }
    card.scrollIntoView({ block: "center" });
    card.classList.remove("locate");
    void card.offsetWidth;
    card.classList.add("locate");
    setTimeout(() => card.classList.remove("locate"), 1200);
  }

  function stopPlayback(message = "", options = {}) {
    const interrupted = playback.currentId || playback.lastLine?.id || "";
    if (interrupted) playback.resumeId = interrupted;
    const lastLine = playback.lastLine;
    playback.active = false;
    playback.queue = [];
    playback.index = 0;
    playback.currentId = "";
    playback.single = false;
    const audio = el("quick-pack-audio");
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      try {
        audio.load();
      } catch {
        /* 忽略播放器重载异常 */
      }
    }
    if (options.reset) {
      playback.lastLine = null;
      playback.lastIndex = 0;
      playback.resumeId = "";
      clearPlayingMark();
      resetNowPlaying();
    } else if (lastLine) {
      showNowPlaying(lastLine, "已停止");
    } else {
      resetNowPlaying();
    }
    if (message) {
      updateAuditionControls();
      setAuditionStatus(message);
    } else {
      updateAudition();
    }
  }

  function finishPlayback() {
    const total = playback.queue.length;
    const lastLine = playback.lastLine;
    const single = playback.single;
    playback.active = false;
    playback.queue = [];
    playback.index = 0;
    playback.currentId = "";
    playback.single = false;
    const audio = el("quick-pack-audio");
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      try {
        audio.load();
      } catch {
        /* 忽略 */
      }
    }
    if (single) {
      playback.resumeId = nextAudioId(lastLine?.id);
      if (lastLine) showNowPlaying(lastLine, "本句完成");
      setAuditionStatus("本句试听完成；点“继续听”从下一句继续");
    } else {
      playback.resumeId = audioLines()[0]?.id || "";
      if (lastLine) showNowPlaying(lastLine, "播放完成");
      setAuditionStatus(`试听完成（${total} 句）；点“继续听”从头循环`);
    }
    updateAuditionControls();
  }

  function playNext() {
    if (!playback.active) return;
    const audio = el("quick-pack-audio");
    if (!audio) return;
    if (playback.index >= playback.queue.length) {
      if (playback.loop && !playback.single && playback.queue.length > 1) {
        playback.index = 0;
        setAuditionStatus("循环试听");
        playNext();
        return;
      }
      finishPlayback();
      return;
    }
    const line = playback.queue[playback.index];
    playback.index += 1;
    playback.currentId = line.id;
    playback.lastLine = line;
    playback.lastIndex = audioIndex(line.id);
    markPlaying(line.id);
    showNowPlaying(line);
    setAuditionStatus(
      playback.single
        ? `单句试听 · 第 ${playback.lastIndex + 1}/${audioLines().length} 句`
        : `正在播放 ${playback.index}/${playback.queue.length}` +
            (playback.loop ? " · 循环" : ""),
    );
    updateAuditionControls();
    audio.src =
      `/api/voice/audio?project=${encodeURIComponent(state.pack.id)}` +
      `&line=${encodeURIComponent(line.id)}&t=${Date.now()}`;
    audio.play()
      .then(() => {
        playback.failures = 0;
      })
      .catch(() => failCurrentLine(line.id));
  }

  function failCurrentLine(lineId) {
    if (!playback.active || !lineId || playback.currentId !== lineId) return;
    playback.currentId = "";
    playback.failures += 1;
    if (playback.failures >= 3) {
      stopPlayback("连续 3 句播放失败，已停止试听");
      return;
    }
    toastMessage("这一句播放失败，已跳过", "bad");
    setTimeout(() => {
      if (playback.active) playNext();
    }, 180);
  }

  function playLines(lines, options = {}) {
    const ready = (lines || []).filter(
      (line) => line?.audio && line.text_source !== "non_voice",
    );
    if (!ready.length) {
      toastMessage(isOriginalMode() ? "还没有可试听的换声，先批量换声" : "还没有可试听的语音，先生成语音", "bad");
      return;
    }
    const start = Math.max(
      0,
      Math.min(Number(options.start || 0), ready.length - 1),
    );
    stopPlayback();
    const preview = el("quick-preview-audio");
    if (preview) preview.pause();
    playback.queue = ready.slice();
    playback.index = start;
    playback.active = true;
    el("quick-audition")?.classList.add("playing");
    playback.single = Boolean(options.single) || ready.length === 1;
    playback.currentId = "";
    playback.failures = 0;
    playNext();
  }

  function continuePlayback() {
    const lines = audioLines();
    if (!lines.length || playback.active) return;
    let start = lines.findIndex((line) => line.id === playback.resumeId);
    if (start < 0) start = 0;
    playLines(lines, { start });
  }

  async function reloadPack() {
    if (!state.pack) return;
    const data = await apiCall(
      `/api/voice/project?project=${encodeURIComponent(state.pack.id)}`,
    );
    state.pack = data.project;
    state.lines = data.project.lines || [];
    renderPack();
  }

  async function synthOnly() {
    if (!state.pack || state.busy) return;
    const original = isOriginalMode();
    const pending = state.lines.filter(original ? needsConversion : needsSynthesis);
    if (!pending.length) {
      toastMessage(original ? "所有句子都已换声，可以直接一键试听" : "有文本的句子都已经生成，可以直接一键试听");
      updateAudition();
      return;
    }
    stopPlayback();
    state.busy = true;
    updateAudition();
    const wrap = el("quick-generate-progress");
    const fill = el("quick-generate-fill");
    const text = el("quick-generate-text");
    if (wrap) wrap.hidden = false;
    if (fill) fill.style.width = "0%";
    if (text) text.textContent = original ? `准备换声 ${pending.length} 句…` : `准备生成 ${pending.length} 句…`;
    try {
      const job = await startJob(
        original ? "/api/voice/convert" : "/api/voice/synthesize",
        original
          ? { project: state.pack.id }
          : {
              project: state.pack.id,
              lines: [],
              force: false,
              versions: 1,
              text_lang: "zh",
              dedupe: true,
            },
        (item) => {
          if (fill) {
            fill.style.width = `${Math.max(0, Math.min(100, Number(item.percent ?? 0)))}%`;
          }
          if (text) {
            text.textContent = `${item.message || (original ? "换声" : "生成语音")}${item.percent == null ? "" : ` · ${Number(item.percent).toFixed(0)}%`}`;
          }
        },
      );
      await reloadPack();
      if (original) {
        const converted = Number(job.result?.converted || 0);
        const errors = job.result?.errors || [];
        toastMessage(
          errors.length
            ? `已换声 ${converted} 句，${errors.length} 句失败，失败行保留原音`
            : `已换声 ${converted} 句，可以一键试听`,
          errors.length ? "warn" : "ok",
        );
      } else {
        const synced = Number(job.result?.synced || 0);
        toastMessage(synced ? `已生成 ${synced} 句，可以一键试听` : "没有生成新的语音");
      }
    } catch (error) {
      toastMessage(error.message, "bad");
    } finally {
      state.busy = false;
      if (wrap) wrap.hidden = true;
      updateAudition();
      renderLines();
    }
  }

  async function importLineAudio(card) {
    if (!state.pack || state.lineBusy) return;
    const lineId = card.dataset.lineId;
    try {
      const chosen = await apiCall("/api/dialog/files", {
        method: "POST",
        body: JSON.stringify({ title: "选择成品音频（WAV）" }),
      });
      const path = (chosen.paths || [])[0];
      if (!path) return;
      const data = await apiCall("/api/voice/take/import", {
        method: "POST",
        body: JSON.stringify({ project: state.pack.id, line: lineId, path }),
      });
      state.pack = data.project;
      state.lines = data.project.lines || state.lines;
      renderPack();
      renderApply();
      toastMessage("已导入音频，可在试听里确认");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function synthLineCard(card) {
    if (!state.pack || state.lineBusy) return;
    const lineId = card.dataset.lineId;
    let line = state.lines.find((item) => item.id === lineId);
    if (!line || line.text_source === "non_voice") return;
    if (!String(line.text || "").trim()) {
      toastMessage("先填写中文台词", "bad");
      return;
    }
    const textarea = card.querySelector("[data-line-text]");
    const text = textarea ? textarea.value.trim() : "";
    if (text !== String(line.text || "").trim()) {
      const saved = await saveLine(card, { silent: true });
      if (!saved) return;
      line = state.lines.find((item) => item.id === lineId) || line;
      card =
        document.querySelector(
          `.quick-line[data-line-id="${CSS.escape(lineId)}"]`,
        ) || card;
    }
    stopPlayback();
    state.lineBusy = lineId;
    updateAudition();
    const button = card.querySelector("[data-synth-line]");
    if (button) {
      button.disabled = true;
      button.textContent = "生成中…";
    }
    try {
      const job = await startJob(
        "/api/voice/synthesize",
        {
          project: state.pack.id,
          lines: [lineId],
          force: true,
          versions: 1,
          text_lang: "zh",
          dedupe: false,
        },
        (item) => {
          setAuditionStatus(
            `正在生成：${item.message || String(line.text || "").slice(0, 20)}`,
          );
        },
      );
      const errors = job.result?.errors || [];
      if (errors.length) throw new Error(errors[0]);
      await reloadPack();
      const fresh = state.lines.find((item) => item.id === lineId);
      if (fresh?.audio) {
        toastMessage("重新生成完成");
        playLines([fresh]);
      } else {
        toastMessage("生成完成，但没有拿到音频，请重试", "bad");
      }
    } catch (error) {
      toastMessage(error.message, "bad");
    } finally {
      state.lineBusy = "";
      updateAudition();
      renderLines();
      const freshCard = document.querySelector(
        `.quick-line[data-line-id="${CSS.escape(lineId)}"]`,
      );
      if (freshCard) {
        const btn = freshCard.querySelector("[data-synth-line]");
        if (btn && btn.disabled) {
          btn.disabled = false;
          btn.textContent = "重新生成";
        }
      }
    }
  }

  function renderPack() {
    const pack = state.pack;
    const summary = el("quick-pack-summary");
    if (!pack || !summary) {
      resetPack();
      return;
    }
    const counts = state.prepare?.text_counts || pack.quick?.text_counts || {};
    const banks = state.prepare?.banks || [];
    const missing = state.prepare?.missing_total ?? state.lines.filter(isMissingText).length;
    const built = Boolean(pack.export?.name);
    const originalMode = pack.quick?.mode === "original";
    const lineScopeLabels = {
      common: "通用",
      air: "空战",
      other: "其他",
      ground: "陆战",
      naval: "海战",
      cockpit: "座舱告警",
    };
    const lineScopeText = (pack.quick?.line_scopes || [])
      .map((scope) => lineScopeLabels[scope] || scope)
      .join(" + ");
    const voiceLines = state.lines.filter((line) => line.text_source === "original");
    const converted = voiceLines.filter((line) => line.audio).length;
    setPill(
      el("quick-pack-pill"),
      originalMode
        ? built
          ? "已生成，可导出/安装"
          : `已准备 · 已换声 ${converted}/${voiceLines.length}`
        : built
          ? missing
            ? `已生成 · ${missing} 句缺文本`
            : "已生成，可导出/安装"
          : missing
            ? `已准备 · ${missing} 句缺文本`
            : "已准备，可生成",
      built ? (missing ? "warn" : "ok") : missing ? "warn" : "ok",
    );
    summary.innerHTML =
      `<div class="quick-summary-row"><strong>${escape(pack.name)}</strong>` +
      `<span class="pill subtle">${state.lines.length} 句</span>` +
      (lineScopeText ? `<span class="pill subtle">${escape(lineScopeText)}</span>` : "") +
      (originalMode
        ? `<span class="pill ${converted === voiceLines.length ? "ok" : "warn"}">${converted}/${voiceLines.length} 句已换声</span>`
        : `<span class="pill ${missing ? "warn" : "ok"}">${missing ? `${missing} 句缺文本` : "文本齐全"}</span>`) +
      `</div>` +
      `<div class="quick-summary-row">` +
      banks
        .map(
          (bank) =>
            `<span class="chip">${escape(bank.label)} · ${bank.lines ?? bank.streams ?? "?"} 句</span>`,
        )
        .join("") +
      `</div>` +
      (originalMode
        ? `<p class="hint small">保留原语言和语气，只换音色；不翻译，不朗读中文。` +
          `首次打包前先批量换声，失败句保留原音。` +
          `${counts.non_voice ? ` · 音效保留原音 ${counts.non_voice} 句` : ""}</p>`
        : `<p class="hint small">文本来源：听写 ${counts.asr || 0} 句 · 缺口译文 ${counts.gap_translation || 0} 句` +
          `${counts.cockpit_text ? ` · 座舱内置 ${counts.cockpit_text} 句` : ""}` +
          `${counts.placeholder_translation ? ` · 占位音译文 ${counts.placeholder_translation} 句` : ""}` +
          `${counts.non_voice ? ` · 音效保留原音 ${counts.non_voice} 句` : ""}` +
          `${missing ? ` · <strong>${missing} 句缺文本，构建时保留原音</strong>` : ""}。` +
          `相同文本只合成一次（当前 ${uniqueTextCount()} 种）。自动听写可能有错字，重点句建议听完原音再改。</p>`);
    el("quick-custom").hidden = !state.lines.length;
    el("quick-step-4")?.classList.add("active");
    renderDirtyNote();
    syncGenerateButton();
    buildCategories();
    renderLines();
  }

  function buildCategories() {
    const counts = new Map();
    for (const line of state.lines) {
      const category = line.source?.category || "其他";
      counts.set(category, (counts.get(category) || 0) + 1);
    }
    state.categories = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    const box = el("quick-categories");
    if (!box) return;
    const active = (value) => (state.filter === value ? " active" : "");
    box.innerHTML =
      `<button class="chip${active("key")}" type="button" data-filter="key">重点</button>` +
      `<button class="chip${active("all")}" type="button" data-filter="all">全部</button>` +
      state.categories
        .map(
          ([category, count]) =>
            `<button class="chip${active(category)}" type="button" data-filter="${escape(category)}">${escape(category)} ${count}</button>`,
        )
        .join("");
  }

  function visibleLines() {
    const query = state.search.trim().toLowerCase();
    return state.lines.filter((line) => {
      const category = line.source?.category || "其他";
      const text = String(line.text || "");
      if (state.filter === "key") {
        if (!KEY_CATEGORIES.has(category) && !isMissingText(line)) return false;
      } else if (state.filter !== "all" && category !== state.filter) {
        return false;
      }
      if (!query) return true;
      return [line.source?.description, line.source?.event, line.text, category]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query));
    });
  }

  function cockpitVoiceFamily(asset) {
    const path = String(asset?.path || "").toLowerCase().replace(/\\/g, "/");
    if (path.includes("/xiao906/")) return "xiao906";
    if (path.includes("/betty/")) return "betty";
    if (path.includes("/rita/")) return "rita";
    return "";
  }

  function cockpitVoicePicker(line) {
    if (String(line.source?.bank || "") !== "aircraft_gui") return "";
    const assets = line.source?.assets || [];
    if (!assets.length || !assets.some((asset) => cockpitVoiceFamily(asset))) return "";
    const labels = { xiao906: "晓906（中）", betty: "贝蒂（英）", rita: "丽塔（俄）" };
    const order = { xiao906: 0, betty: 1, rita: 2 };
    const options = assets
      .map((asset, index) => ({ asset, index, family: cockpitVoiceFamily(asset) }))
      .sort(
        (a, b) =>
          (order[a.family] ?? 9) - (order[b.family] ?? 9) || a.index - b.index
      )
      .map(
        ({ asset, index, family }) =>
          `<option value="${index}">${escape(
            asset.voice_label || labels[family] || asset.file || "原声"
          )}</option>`
      )
      .join("");
    return (
      `<select class="input select quick-source-voice" data-source-voice aria-label="原音语种">` +
      options +
      `</select>`
    );
  }

  function lineSourceBadge(line) {
    const text = String(line.text || "").trim();
    if (line.text_source === "non_voice") {
      return '<span class="pill subtle quick-source-badge">保留原音</span>';
    }
    if (line.text_source === "original") {
      return line.audio
        ? '<span class="pill ok quick-source-badge">已换声</span>'
        : '<span class="pill subtle quick-source-badge">原语言</span>';
    }
    if (!text) return '<span class="pill bad quick-source-badge">缺文本</span>';
    if (line.text_source === "gap_translation") {
      return '<span class="pill warn quick-source-badge">首轮译文</span>';
    }
    if (line.text_source === "placeholder_translation") {
      return '<span class="pill warn quick-source-badge">占位音译文</span>';
    }
    if (line.text_source === "manual") {
      return '<span class="pill subtle quick-source-badge">已修改</span>';
    }
    if (line.text_source === "cockpit_text") {
      if (line.source?.kind === "effect") {
        return '<span class="pill warn quick-source-badge">音效改语音</span>';
      }
      if (line.source?.kind === "voice") {
        return '<span class="pill subtle quick-source-badge">炮手语音</span>';
      }
      return '<span class="pill subtle quick-source-badge">内置告警词</span>';
    }
    return '<span class="pill subtle quick-source-badge">自动听写</span>';
  }

  function tuningBadge(line) {
    if (line.text_source === "non_voice") return "";
    const tuning = line.tuning && typeof line.tuning === "object" ? line.tuning : {};
    const parts = [];
    const reference = String(tuning.reference || "");
    if (reference) {
      const files = state.pack?.reference?.files || [];
      const index = files.indexOf(reference);
      parts.push(index >= 0 ? `语气 ${index + 1}` : "自定义语气");
    }
    const speed = Number(tuning.speed ?? 1);
    if (Math.abs(speed - 1) > 0.001) parts.push(`${speed.toFixed(2)}×`);
    const gain = Number(tuning.gain_db ?? 0);
    if (Math.abs(gain) > 0.001) {
      parts.push(`${gain > 0 ? "+" : ""}${gain.toFixed(1)} dB`);
    }
    if (!parts.length) return "";
    const title =
      `参考：${reference || "项目默认"} · 语速 ${speed.toFixed(2)}× · ` +
      `音量 ${gain > 0 ? "+" : ""}${gain.toFixed(1)} dB`;
    return `<span class="pill subtle quick-tune-badge" title="${escape(title)}">${escape(parts.join(" · "))}</span>`;
  }

  function renderLines() {
    const box = el("quick-lines");
    if (!box) return;
    const lines = visibleLines();
    const shown = lines.slice(0, state.limit);
    if (!lines.length) {
      box.innerHTML = '<p class="muted small">这个筛选下没有台词。</p>';
      el("quick-more").hidden = true;
    } else {
      box.innerHTML = shown
        .map((line) => {
          const source = line.source || {};
          const text = String(line.text || "");
          const nonVoice = line.text_source === "non_voice";
          const original = line.text_source === "original";
          const ready = Boolean(line.audio);
          const variant = recordingVariant(line);
          const seconds = Number(line.duration_sec || 0);
          const audioText = nonVoice
            ? "不参与合成"
            : original
              ? ready
                ? `已换声${seconds ? ` ${seconds.toFixed(2)}s` : ""}`
                : "未换声"
              : ready
                ? `已生成${seconds ? ` ${seconds.toFixed(2)}s` : ""}`
                : "未生成";
          const textBlock = nonVoice
            ? `<div class="quick-line-label">${escape(source.category || "音效 / 非语音")}</div>` +
              `<p class="hint small">${escape(line.text_note || "非人声音效；构建时保留游戏原音，不参与合成。")}</p>`
            : original
              ? `<div class="quick-line-label">原语言换声（不朗读中文）</div>` +
                `<p class="hint small">保留游戏原语言和语气，只换成训练声线；不需要修改文本。</p>`
              : `<div class="quick-line-label">中文台词（会读这句）</div>` +
                `<textarea class="input textarea quick-line-text" rows="2" data-line-text>${escape(text)}</textarea>`;
          const voicePicker = cockpitVoicePicker(line);
          const sourceButton = `<button class="btn ghost small" type="button" data-play-source>试听原音</button>`;
          const actions = nonVoice
            ? sourceButton
            : original
              ? `<button class="btn ghost small" type="button" data-play-line${ready ? "" : " disabled"}>试听换声</button>` +
                `<button class="btn ghost small" type="button" data-tune-line title="调整这一句的音量">音量</button>` +
                voicePicker +
                sourceButton
              : `<button class="btn ghost small" type="button" data-save-line>保存文本</button>` +
                `<button class="btn ghost small" type="button" data-synth-line${state.lineBusy ? " disabled" : ""}>${ready ? "重新生成" : "生成语音"}</button>` +
                `<button class="btn ghost small" type="button" data-play-line${ready ? "" : " disabled"}>试听</button>` +
                `<button class="btn ghost small" type="button" data-import-line title="用现成 WAV 替换这一句">导入音频</button>` +
                `<button class="btn ghost small" type="button" data-tune-line title="调整这一句的语气参考、语速和音量">语气/音量</button>` +
                voicePicker +
                sourceButton;
          return (
            `<article class="quick-line${isMissingText(line) ? " missing" : ""}" data-line-id="${escape(line.id)}">` +
            `<div class="quick-line-head">` +
            `<span class="pill subtle">${escape(source.category || "其他")}</span>` +
            `<span class="quick-line-title">${escape(source.description || source.event || "未命名")}` +
            `${variant && !String(source.description || "").includes(variant) ? ` · ${escape(variant)}` : ""}</span>` +
            lineSourceBadge(line) +
            tuningBadge(line) +
            `<span class="quick-line-audio ${nonVoice ? "" : ready ? "ready" : "missing"}">${audioText}</span>` +
            `</div>` +
            `<div class="quick-line-name mono">${escape(source.event || line.target_file || "")}</div>` +
            textBlock +
            `<div class="quick-line-actions">` +
            actions +
            `<span class="quick-line-note">${escape(line.text_note || "自动初稿，可修改")}</span>` +
            `</div>` +
            `</article>`
          );
        })
        .join("");
      el("quick-more").hidden = lines.length <= state.limit;
    }
    const missing = state.lines.filter(isMissingText).length;
    el("quick-custom-hint").textContent = isOriginalMode()
      ? `原语言换声不需要文本；先批量换声再逐句试听，失败或没换的行会保留游戏原音。默认只显示重点类别，点“全部”查看所有句。`
      : `默认只显示击杀、RWR/告警、灭火、装填、乘员、火力支援、座舱告警等重点类别；点“全部”查看所有句。` +
        `${missing ? ` 当前还有 ${missing} 句没有文本。` : ""} 文本来自自动听写/首轮译文，重要句请点“试听原音”核对。` +
        ` 座舱告警分晓906（中）/贝蒂（英）/丽塔（俄）三套原声，可下拉切换试听；第 3 步能逐条勾选，炮手语音、击落“叮”声和助攻提示也可单独加。中文模式会把选中的条目换成你的中文声线。`;
    syncGenerateButton();
    updateAudition();
    const current = playback.currentId || playback.lastLine?.id;
    if (current) {
      markPlaying(current);
    }
  }

  function openTuning(card) {
    if (!state.pack || !window.WTLineTuning) return;
    const line = state.lines.find((item) => item.id === card.dataset.lineId);
    if (!line) return;
    const textarea = card.querySelector("[data-line-text]");
    const text = textarea ? textarea.value.trim() : line.text;
    window.WTLineTuning.open({
      project: state.pack,
      line,
      text,
      scope: visibleLines()
        .filter((item) => item.text_source !== "non_voice")
        .map((item) => item.id),
      original:
        line.text_source === "original" || state.pack.quick?.mode === "original",
    });
  }

  async function saveLine(card, options = {}) {
    if (!state.pack) return false;
    const lineId = card.dataset.lineId;
    const textarea = card.querySelector("[data-line-text]");
    if (!textarea) return false;
    const text = textarea.value.trim();
    const live = state.lines.find((line) => line.id === lineId);
    const previous = String(live?.text || "").trim();
    if (text === previous) {
      if (!options.silent) toastMessage("文本没有变化");
      return true;
    }
    try {
      const data = await apiCall("/api/voice/line/update", {
        method: "POST",
        body: JSON.stringify({
          project: state.pack.id,
          line: lineId,
          text,
        }),
      });
      state.pack = data.project;
      if (live) {
        live.text = text;
        live.text_source = text ? "manual" : "";
        live.text_note = text ? "手动修改" : "";
        live.audio = "";
        live.status = "draft";
        live.duration_sec = 0;
        live.error = "";
        delete live.takes;
        delete live.selected_take;
      }
      delete state.pack.export;
      if (playback.active) {
        stopPlayback("台词已修改，需要重新生成");
      }
      downloadLinks();
      renderPack();
      if (!options.silent) toastMessage("文本已保存，可以重新生成");
      return true;
    } catch (error) {
      toastMessage(error.message, "bad");
      return false;
    }
  }

  function playSource(card) {
    if (!state.pack) return;
    if (playback.active) stopPlayback();
    const picker = card.querySelector("[data-source-voice]");
    const asset = picker ? `&asset=${encodeURIComponent(picker.value)}` : "";
    const url =
      `/api/audio/source?project=${encodeURIComponent(state.pack.id)}` +
      `&line=${encodeURIComponent(card.dataset.lineId)}` +
      asset;
    const audio = new Audio(url);
    audio.play().catch(() => toastMessage("原音播放失败，可能这张表没有这条原音", "bad"));
  }

  function downloadLinks() {
    const info = state.pack?.export;
    const box = el("quick-download");
    if (!info?.name) {
      state.downloadSignature = "";
      state.exportInfo = null;
      state.installInfo = null;
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    const signature = `${state.pack.id}:${info.name}`;
    if (state.downloadSignature !== signature) {
      state.downloadSignature = signature;
      state.exportInfo = null;
      state.installInfo = null;
    }
    const files = Array.isArray(info.files) ? info.files : [];
    const banks = Array.isArray(info.banks) ? info.banks : [];
    const count = files.length || banks.length;
    const gameReady = Boolean(state.options?.game_ready);
    const disabled = state.actionBusy ? " disabled" : "";
    const installed = state.installInfo;
    const exported = state.exportInfo;
    let result = "";
    if (installed) {
      result +=
        `<div class="quick-download-result">已安装 ${Number(installed.pack?.banks?.length || count)} 个 bank` +
        ` 到 <span class="mono">${escape(installed.installed?.mod_dir || "sound/mod")}</span>。` +
        ` 启动游戏后看左上角是否显示“已启用自定义音效”。</div>`;
    }
    if (exported) {
      result +=
        `<div class="quick-download-result">安装包已导出：` +
        `<span class="mono">${escape(exported.path)}</span></div>`;
    }
    const hint = !gameReady
      ? "还没有选择战雷目录，一键安装不可用；先到工作台选择游戏目录。"
      : installed
        ? "以后重新构建时，再点“一键安装”会覆盖同名 bank，旧文件会先备份。"
        : "“一键安装”会写入游戏 sound/mod，并把 enable_mod、fmod_sound_enable 改成 yes；覆盖同名 bank 前会先备份。";
    box.hidden = false;
    box.innerHTML =
      `<div class="quick-download-head">` +
      `<strong>安装包已生成</strong>` +
      `<span class="hint small">${escape(info.name)} · ${count} 个 bank · ${formatBytes(info.bytes)}</span>` +
      `</div>` +
      `<div class="quick-download-actions">` +
      `<button class="btn" type="button" data-quick-action="export"${disabled}>导出安装包</button>` +
      `<button class="btn" type="button" data-quick-action="install"${disabled || !gameReady ? " disabled" : ""}>一键安装到游戏</button>` +
      (exported
        ? `<button class="btn ghost" type="button" data-quick-action="open-export"${disabled}>打开导出文件夹</button>`
        : "") +
      `</div>` +
      result +
      `<p class="hint small quick-download-hint">${hint}</p>`;
  }

  async function exportPackage() {
    const info = state.pack?.export;
    if (!info?.name || state.actionBusy) return;
    state.actionBusy = true;
    downloadLinks();
    try {
      const data = await apiCall("/api/voice/package/export", {
        method: "POST",
        body: JSON.stringify({ project: state.pack.id, name: info.name }),
      });
      state.exportInfo = data;
      toastMessage(`安装包已导出：${data.path}`);
    } catch (error) {
      toastMessage(error.message, "bad");
    } finally {
      state.actionBusy = false;
      downloadLinks();
    }
  }

  async function installPackage() {
    const info = state.pack?.export;
    if (!info?.name || state.actionBusy) return;
    if (!state.options?.game_ready) {
      toastMessage("先到工作台选择战雷目录，再一键安装", "bad");
      return;
    }
    state.actionBusy = true;
    state.installInfo = null;
    downloadLinks();
    try {
      const data = await apiCall("/api/voice/package/install", {
        method: "POST",
        body: JSON.stringify({ project: state.pack.id, name: info.name }),
      });
      state.installInfo = data;
      const count = data.pack?.banks?.length || 0;
      toastMessage(`已安装 ${count} 个 bank，启动游戏后确认左上角提示`);
    } catch (error) {
      toastMessage(error.message, "bad");
    } finally {
      state.actionBusy = false;
      downloadLinks();
      renderApply();
    }
  }

  async function openExportFolder() {
    if (state.actionBusy) return;
    try {
      await apiCall("/api/system/open-folder", {
        method: "POST",
        body: JSON.stringify({ target: "exports" }),
      });
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function generate() {
    if (!state.pack || state.busy) return;
    const original = isOriginalMode();
    if (!original) {
      const missing = state.lines.filter(isMissingText).length;
      if (missing && !confirm(`还有 ${missing} 句没有文本，构建时会保留这些行的原音。继续？`)) return;
    }
    const pending = original ? [] : state.lines.filter(needsSynthesis);
    const pendingConvert = original ? state.lines.filter(needsConversion) : [];
    const unique = new Set(
      pending.map((line) => String(line.text || "").trim()).filter(Boolean),
    ).size;
    const prompt = original
      ? pendingConvert.length
        ? `还有 ${pendingConvert.length} 句没换声。先批量换声，再构建原语言包？`
        : "所有句子都已换声，直接构建原语言包？"
      : pending.length
        ? `还有 ${pending.length} 句没生成。相同文本只合成一次（约 ${unique} 种），预计 ${humanMinutes(unique)}。完成后继续构建？`
        : "有文本的句子都已生成，直接构建语音包？";
    if (!confirm(prompt)) return;
    stopPlayback();
    state.busy = true;
    updateAudition();
    el("quick-generate-btn").disabled = true;
    el("quick-generate-progress").hidden = false;
    el("quick-generate-text").textContent = original
      ? pendingConvert.length ? "准备批量换声…" : "准备构建原语言包…"
      : pending.length ? "准备合成…" : "语音已生成，准备构建…";
    el("quick-generate-fill").style.width = (original ? pendingConvert.length : pending.length) ? "0%" : "85%";
    try {
      let errors = [];
      if (original && pendingConvert.length) {
        const convert = await startJob(
          "/api/voice/convert",
          { project: state.pack.id },
          (job) => {
            el("quick-generate-text").textContent = `换声：${job.message || "处理中…"}`;
            const percent = Number(job.percent ?? 0) * 0.85;
            el("quick-generate-fill").style.width = `${Math.max(0, Math.min(85, percent))}%`;
          },
        );
        errors = convert.result?.errors || [];
        if (!convert.result?.converted) {
          throw new Error(errors[0] || "没有换声成功的台词");
        }
        if (errors.length) {
          if (!confirm(`有 ${errors.length} 句换声失败；其余已成功。仍继续打包吗？`)) {
            el("quick-generate-text").textContent = "已停在打包前，失败行会保留游戏原音。";
            return;
          }
        }
      }
      if (!original && pending.length) {
        const synth = await startJob(
          "/api/voice/synthesize",
          {
            project: state.pack.id,
            lines: [],
            force: false,
            versions: 1,
            text_lang: "zh",
            dedupe: true,
          },
          (job) => {
            el("quick-generate-text").textContent = `合成：${job.message || "处理中…"}`;
            const percent = Number(job.percent ?? 0) * 0.85;
            el("quick-generate-fill").style.width = `${Math.max(0, Math.min(85, percent))}%`;
          },
        );
        errors = synth.result?.errors || [];
        if (!synth.result?.synced) {
          throw new Error(errors[0] || "没有合成成功的台词");
        }
        if (errors.length) {
          if (!confirm(`有 ${errors.length} 句合成失败；其余已成功。仍继续打包吗？`)) {
            el("quick-generate-text").textContent = "已停在打包前，失败行可以回高级编辑重试。";
            return;
          }
        }
      }
      el("quick-generate-text").textContent = original ? "换声完成，开始构建 bank…" : "合成完成，开始构建 bank…";
      el("quick-generate-fill").style.width = "88%";
      await startJob(
        "/api/voice/build",
        { project: state.pack.id },
        (job) => {
          el("quick-generate-text").textContent = `构建：${job.message || "处理中…"}`;
          el("quick-generate-fill").style.width = `${Math.max(85, Math.min(99, Number(job.percent ?? 90)))}%`;
        },
      );
      const data = await apiCall(`/api/voice/project?project=${encodeURIComponent(state.pack.id)}`);
      state.pack = data.project;
      state.lines = data.project.lines || state.lines;
      el("quick-generate-fill").style.width = "100%";
      el("quick-generate-text").textContent = "完成，可以导出安装包或一键安装";
      downloadLinks();
      toastMessage("语音包已生成");
      renderApply();
    } catch (error) {
      el("quick-generate-text").textContent = error.message;
      toastMessage(error.message, "bad");
    } finally {
      state.busy = false;
      renderPack();
    }
  }

  async function cancelJob() {
    try {
      await apiCall("/api/jobs/current/cancel", { method: "POST", body: "{}" });
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  function setQuickWorkflowStep(stepId) {
    const bar = el("quick-workflow");
    if (!bar) return;
    const steps = [...bar.querySelectorAll(".wf-step")];
    const index = steps.findIndex((button) => button.dataset.target === stepId);
    if (index < 0) return;
    steps.forEach((button, i) => button.classList.toggle("active", i === index));
    const stateText = el("quick-wf-state");
    if (stateText) stateText.textContent = `第 ${index + 1} 步 / 共 4 步`;
  }

  const QUICK_STEPS = ["quick-step-1", "quick-step-2", "quick-step-3", "quick-step-4"];

  function showQuickStep(stepId, scroll = true) {
    if (!QUICK_STEPS.includes(stepId)) return;
    state.quickStep = stepId;
    for (const id of QUICK_STEPS) {
      const card = el(id);
      if (card) card.hidden = id !== stepId;
    }
    setQuickWorkflowStep(stepId);
    renderCoach(stepId);
    if (scroll) {
      el("quick-workflow")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function bind() {
    el("quick-workflow")?.addEventListener("click", (event) => {
      const button = event.target.closest(".wf-step");
      if (!button) return;
      showQuickStep(button.dataset.target);
    });
    el("workbench-wizard")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-quick-step]");
      if (!button) return;
      showQuickStep(button.dataset.quickStep);
    });
    el("quick-engine-install")?.addEventListener("click", installEngine);
    el("quick-engine-cancel")?.addEventListener("click", cancelEngine);

    window.addEventListener("wt:refresh-wizard", () => {
      if (!state.initialized) return;
      loadEngine();
      loadProjects().catch((error) => toastMessage(error.message, "bad"));
      loadOptions();
      loadBankCounts();
    });
    window.addEventListener("wt:line-tuning-saved", (event) => {
      const detail = event.detail || {};
      if (!detail.project || detail.project.id !== state.pack?.id) return;
      if (detail.needsSynthesis && playback.active) {
        stopPlayback("语气或语速已修改，需要重新生成");
      }
      state.pack = detail.project;
      state.lines = detail.project.lines || state.lines;
      downloadLinks();
      renderPack();
    });
    el("quick-project-select")?.addEventListener("change", (event) => {
      selectProject(event.target.value).catch((error) => toastMessage(error.message, "bad"));
    });
    el("quick-create-btn")?.addEventListener("click", createProject);
    el("quick-import-btn")?.addEventListener("click", importReference);
    el("quick-add-files-btn")?.addEventListener("click", addMaterialFiles);
    el("quick-clean-btn")?.addEventListener("click", cleanMaterials);
    el("quick-apply-btn")?.addEventListener("click", applyToGame);
    el("quick-restore-btn")?.addEventListener("click", restoreInstall);
    el("quick-ref-list")?.addEventListener("click", async (event) => {
      const play = event.target.closest("[data-ref-play]");
      if (play) {
        if (!state.project) return;
        const url =
          `/api/voice/reference/audio?project=${encodeURIComponent(state.project.id)}` +
          `&file=${encodeURIComponent(play.dataset.refPlay)}`;
        new Audio(url).play().catch(() => toastMessage("试听失败，素材可能已被删除", "bad"));
        return;
      }
      const remove = event.target.closest("[data-ref-del]");
      if (remove && state.project) {
        if (!confirm(`删除素材 ${remove.dataset.refDel}？`)) return;
        try {
          const data = await apiCall("/api/voice/reference/delete", {
            method: "POST",
            body: JSON.stringify({
              project: state.project.id,
              name: remove.dataset.refDel,
            }),
          });
          state.project = data.project;
          renderStep1();
          toastMessage("已删除素材");
        } catch (error) {
          toastMessage(error.message, "bad");
        }
      }
    });
    el("quick-detect-btn")?.addEventListener("click", () => detectPrompt(false));
    el("quick-train-btn")?.addEventListener("click", trainModel);
    el("quick-import-model-btn")?.addEventListener("click", importModelWeights);
    el("quick-train-cancel")?.addEventListener("click", cancelJob);
    el("quick-rvc-train")?.addEventListener("click", rvcAction);
    el("quick-rvc-import")?.addEventListener("click", importRvcModel);
    el("quick-preview-btn")?.addEventListener("click", preview);
    el("quick-prompt")?.addEventListener("input", renderStep1);
    el("quick-scope-common")?.addEventListener("change", renderStep3);
    el("quick-scope-air")?.addEventListener("change", renderStep3);
    el("quick-scope-other")?.addEventListener("change", renderStep3);
    el("quick-scope-ground")?.addEventListener("change", renderStep3);
    el("quick-scope-naval")?.addEventListener("change", renderStep3);
    el("quick-scope-cockpit")?.addEventListener("change", renderStep3);
    el("quick-cockpit-picker-body")?.addEventListener("change", (event) => {
      const id = event.target?.dataset?.cockpitEvent;
      if (!id) return;
      ensureCockpitSelection();
      if (event.target.checked) state.cockpitSelection.add(id);
      else state.cockpitSelection.delete(id);
      renderStep3();
    });
    el("quick-cockpit-all")?.addEventListener("click", () => {
      ensureCockpitSelection();
      for (const item of cockpitItems()) {
        if (item.kind === "vws") state.cockpitSelection.add(item.event_id);
      }
      renderStep3();
    });
    el("quick-cockpit-none")?.addEventListener("click", () => {
      ensureCockpitSelection();
      state.cockpitSelection.clear();
      renderStep3();
    });
    el("quick-cockpit-default")?.addEventListener("click", () => {
      state.cockpitSelection = new Set(
        cockpitItems()
          .filter((item) => item.default_selected)
          .map((item) => item.event_id),
      );
      renderStep3();
    });
    el("quick-modes")?.addEventListener("change", (event) => {
      const mode = event.target.closest("[data-mode]")?.dataset.mode;
      if (mode) {
        state.mode = mode;
        renderStep3();
      }
    });
    el("quick-nations")?.addEventListener("change", (event) => {
      const card = event.target.closest("[data-nation]");
      if (card) toggleNation(card.dataset.nation, event.target.checked);
    });
    el("quick-nations-all")?.addEventListener("click", () => {
      const scopes = new Set(selectedBankScopes());
      for (const nation of state.options?.nations || []) {
        const usable = (nation.banks || []).some(
          (bank) => scopes.has(bank.scope) && bank.available,
        );
        if (usable) state.selectedNations.add(nation.id);
      }
      renderStep3();
    });
    el("quick-nations-clear")?.addEventListener("click", () => {
      state.selectedNations.clear();
      renderStep3();
    });
    el("quick-language-banks")?.addEventListener("change", (event) => {
      const card = event.target.closest("[data-bank]");
      if (card) toggleBank(card.dataset.bank, event.target.checked);
    });
    el("quick-language-chips")?.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-language]");
      if (!chip) return;
      const group = languageGroups().find((item) => item.id === chip.dataset.language);
      if (!group) return;
      const effective = effectiveBankIds();
      if (group.banks.every((bank) => effective.has(bank.bank))) {
        toastMessage(`${group.label}已经包含在成品里`);
        return;
      }
      for (const bank of group.banks) state.selectedBanks.add(bank.bank);
      renderStep3();
    });
    el("quick-banks-common")?.addEventListener("click", () => {
      for (const bank of state.options?.languages || []) {
        if (bank.scope === "common" && selectedBankScopes().includes("common")) {
          state.selectedBanks.add(bank.bank);
        }
      }
      renderStep3();
    });
    el("quick-banks-ground")?.addEventListener("click", () => {
      for (const bank of state.options?.languages || []) {
        if (bank.scope === "ground" && selectedBankScopes().includes("ground")) {
          state.selectedBanks.add(bank.bank);
        }
      }
      renderStep3();
    });
    el("quick-banks-naval")?.addEventListener("click", () => {
      for (const bank of state.options?.languages || []) {
        if (bank.scope === "naval" && selectedBankScopes().includes("naval")) {
          state.selectedBanks.add(bank.bank);
        }
      }
      renderStep3();
    });
    el("quick-banks-clear")?.addEventListener("click", () => {
      state.selectedBanks.clear();
      renderStep3();
    });
    el("quick-prepare-btn")?.addEventListener("click", preparePack);
    el("quick-generate-btn")?.addEventListener("click", generate);
    el("quick-generate-cancel")?.addEventListener("click", cancelJob);
    el("quick-categories")?.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-filter]");
      if (!chip) return;
      state.filter = chip.dataset.filter;
      state.limit = 60;
      document.querySelectorAll("#quick-categories [data-filter]").forEach((item) => {
        item.classList.toggle("active", item === chip);
      });
      renderLines();
    });
    el("quick-search")?.addEventListener("input", (event) => {
      state.search = event.target.value;
      state.limit = 60;
      renderLines();
    });
    el("quick-more")?.addEventListener("click", () => {
      state.limit += 60;
      renderLines();
    });
    el("quick-lines")?.addEventListener("click", (event) => {
      const card = event.target.closest(".quick-line");
      if (!card) return;
      if (event.target.closest("[data-save-line]")) saveLine(card);
      else if (event.target.closest("[data-synth-line]")) synthLineCard(card);
      else if (event.target.closest("[data-import-line]")) importLineAudio(card);
      else if (event.target.closest("[data-tune-line]")) openTuning(card);
      else if (event.target.closest("[data-play-line]")) {
        const line = state.lines.find((item) => item.id === card.dataset.lineId);
        if (line) playLines([line]);
      } else if (event.target.closest("[data-play-source]")) playSource(card);
    });
    el("quick-play-all")?.addEventListener("click", () => playLines(audioLines()));
    el("quick-compare-source")?.addEventListener("click", compareSource);
    el("quick-presets")?.addEventListener("click", (event) => {
      const card = event.target.closest("[data-preset]");
      if (card) applyPreset(card.dataset.preset);
    });
    el("quick-play-continue")?.addEventListener("click", continuePlayback);
    el("quick-stop-all")?.addEventListener("click", () => stopPlayback("已停止试听"));
    el("quick-locate-current")?.addEventListener("click", locateCurrentLine);
    el("quick-loop-audition")?.addEventListener("change", (event) => {
      playback.loop = Boolean(event.target.checked);
      localStorage.setItem("wt.quick.loop", playback.loop ? "1" : "0");
      setAuditionStatus(playback.loop ? "已开启循环试听" : "已关闭循环试听");
    });
    el("quick-synth-only")?.addEventListener("click", synthOnly);
    el("quick-download")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-quick-action]");
      if (!button || button.disabled || state.actionBusy) return;
      const action = button.dataset.quickAction;
      if (action === "export") exportPackage();
      else if (action === "install") installPackage();
      else if (action === "open-export") openExportFolder();
    });
    const packAudio = el("quick-pack-audio");
    packAudio?.addEventListener("ended", () => {
      if (playback.active) playNext();
    });
    packAudio?.addEventListener("error", () => {
      if (playback.active) failCurrentLine(playback.currentId);
      else if (packAudio.src) toastMessage("播放失败：音频可能不存在", "bad");
    });
    el("quick-lines")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        const card = event.target.closest(".quick-line");
        if (card) saveLine(card);
      }
    });
    el("quick-advanced-link")?.addEventListener("click", () => {
      if (state.pack?.id) localStorage.setItem("wt.voice.project", state.pack.id);
    });
  }

  async function loadOptions() {
    try {
      const data = await apiCall("/api/quick/options");
      state.options = data;
      renderStep3();
    } catch (error) {
      state.options = null;
      renderStep3();
      toastMessage(error.message, "bad");
    }
  }

  async function init() {
    if (state.initialized || !el("workbench-wizard")) return;
    state.initialized = true;
    bind();
    await loadEngine();
    // 先拿到语音表再恢复项目：座舱默认勾选和“范围是否变化”都要用到它。
    await loadOptions();
    try {
      await loadProjects();
    } catch (error) {
      toastMessage(error.message, "bad");
    }
    loadBankCounts();
    showQuickStep("quick-step-1", false);
  }

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("hashchange", () => {
    if ((location.hash === "#workbench" || location.hash === "#quick") && state.initialized) {
      loadEngine();
      loadProjects().catch(() => {});
    }
  });
})();
