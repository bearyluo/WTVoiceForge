/* M2：声音项目、参考音频、台词表与 GPT-SoVITS 合成 */
(() => {
  "use strict";

  const state = {
    projects: [],
    current: null,
    build: null,
    training: null,
    engine: null,
    job: null,
    pollTimer: null,
    componentTimer: null,
    localizedBanks: [],
    localizedInfo: null,
    localizedError: "",
    coverage: null,
    coverageError: "",
    initialized: false,
  };

  const playback = {
    active: false,
    queue: [],
    index: 0,
    currentId: "",
    lastLine: null,
    resumeId: "",
    failures: 0,
    loop: localStorage.getItem("wt.voice.loop") !== "0",
  };

  const el = (id) => document.getElementById(id);
  const escape = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);

  const formatLocal = (value) => {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { hour12: false });
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

  function toastMessage(message, tone = "ok") {
    if (typeof toast === "function") toast(message, tone);
    else console.log(message);
  }

  function setPill(node, text, tone = "") {
    if (!node) return;
    node.textContent = text;
    node.className = `pill ${tone}`.trim();
  }

  function statusLabel(line) {
    return {
      draft: "未合成",
      syncing: "合成中",
      ready: "可试听",
      error: "失败",
    }[line.status] || "未合成";
  }

  function statusTone(line) {
    if (line.status === "ready") return "ok";
    if (line.status === "error") return "bad";
    if (line.status === "syncing") return "warn";
    return "subtle";
  }

  function renderProjects() {
    const select = el("voice-project-select");
    if (!select) return;
    if (!state.projects.length) {
      select.innerHTML = '<option value="">还没有声音项目</option>';
      el("voice-empty").hidden = false;
      el("voice-workspace").hidden = true;
      return;
    }
    select.innerHTML = state.projects
      .map(
        (project) =>
          `<option value="${escape(project.id)}">${escape(project.name)} · ${project.line_count} 句</option>`,
      )
      .join("");
    const wanted = state.current?.id || localStorage.getItem("wt.voice.project") || state.projects[0].id;
    select.value = state.projects.some((item) => item.id === wanted) ? wanted : state.projects[0].id;
  }

  function renderEngine() {
    const box = el("voice-engine-box");
    const pill = el("voice-engine-pill");
    if (!box) return;
    const engine = state.engine || {};
    if (engine.available) {
      setPill(pill, "GPT-SoVITS 已就绪", "ok");
      box.innerHTML =
        `<span class="pill ok">已安装</span> <span class="mono small">${escape(engine.path || "")}</span>` +
        `<p class="hint small">第一次合成会启动本地引擎，通常需要 30–90 秒；之后会快很多。</p>`;
      return;
    }
    setPill(pill, "GPT-SoVITS 未安装", "warn");
    box.innerHTML =
      `<span class="pill warn">未安装</span>` +
      `<p class="hint">一键下载 GPT-SoVITS v4 Windows 整合包（约 7.5GB，装到数据目录）。` +
      `默认走 hf-mirror.com 镜像，失败自动切换官方源；下载、校验和解压都有进度条。</p>` +
      `<p class="hint small">来源：<span class="mono">huggingface.co/lj1995/GPT-SoVITS-windows-package</span>` +
      ` · 镜像：<span class="mono">hf-mirror.com</span></p>` +
      `<button class="btn" type="button" data-install-engine="1">一键下载并安装</button>`;
  }

  function renderProject() {
    const project = state.current;
    const empty = el("voice-empty");
    const workspace = el("voice-workspace");
    if (!project) {
      empty.hidden = false;
      workspace.hidden = true;
      return;
    }
    empty.hidden = true;
    workspace.hidden = false;
    el("voice-title").textContent = project.name;
    const refs = project.reference?.files || [];
    const lines = project.lines || [];
    const ready = lines.filter((line) => line.status === "ready").length;
    el("voice-meta").textContent = `${refs.length} 段参考音频 · ${lines.length} 句台词 · ${ready} 句已合成`;
    setPill(
      el("voice-status-pill"),
      refs.length && project.reference?.prompt_text ? "可以合成" : "还缺参考信息",
      refs.length && project.reference?.prompt_text ? "ok" : "warn",
    );

    el("voice-ref-list").innerHTML = refs.length
      ? refs
          .map(
            (name) =>
              `<span class="chip">${escape(name)}</span>`,
          )
          .join("")
      : '<span class="muted small">还没有参考音频</span>';

    const promptSelect = el("voice-prompt-file");
    promptSelect.innerHTML = refs
      .map(
        (name) =>
          `<option value="${escape(name)}">${escape(name)}</option>`,
      )
      .join("");
    if (project.reference?.prompt_file) promptSelect.value = project.reference.prompt_file;
    el("voice-prompt").value = project.reference?.prompt_text || "";
    el("voice-prompt-lang").value = project.reference?.prompt_lang || "zh";
    el("voice-export-link").href =
      `/api/voice/export?project=${encodeURIComponent(project.id)}`;

    renderReferenceTexts();
    renderModel();
    renderBuild();
    renderLocalized();
    fillLocalizedSources();
    renderEngine();
    renderLines();
  }

  function renderBuild() {
    const build = state.build || { banks: [], ready: false, ready_lines: 0 };
    const pill = el("voice-build-pill");
    const list = el("voice-build-banks");
    const button = el("voice-build-btn");
    const hint = el("voice-build-hint");
    const link = el("voice-download-link");
    const zipLink = el("voice-download-zip-link");
    const banks = build.banks || [];
    setPill(pill, build.ready ? "可构建" : "未就绪", build.ready ? "ok" : "warn");
    if (list) {
      list.innerHTML = banks.length
        ? banks.map((item) => `<span class="chip">${escape(item.name)} · ${item.lines} 句</span>`).join("")
        : '<span class="muted small">还没有可构建的台词。</span>';
    }
    if (button) button.disabled = !build.ready;
    if (hint) {
      const fmod = build.fmod ? `FMOD：${build.fmod.version}` : "未检测到 FMOD Studio CLI";
      const parts = [fmod];
      if (build.fmod && !String(build.fmod.version).includes("2.02.22")) {
        parts.push("官方工程当前标注 2.02.22；版本不同且游戏不加载时，换用官方版本");
      }
      if (build.ready_lines) parts.push(`已合成 ${build.ready_lines} 句`);
      if (build.unbound_lines) parts.push(`${build.unbound_lines} 句未绑定事件，将被跳过`);
      if (!build.ready && build.reason) parts.push(build.reason);
      hint.textContent = parts.join(" · ");
    }
    const exportInfo = state.current?.export;
    if (link) {
      if (exportInfo?.name) {
        const base =
          `/api/voice/package?project=${encodeURIComponent(state.current.id)}` +
          `&name=${encodeURIComponent(exportInfo.name)}`;
        link.hidden = false;
        link.href = base;
        link.textContent = "下载语音包";
        link.title = `下载 ${exportInfo.name}`;
        if (zipLink) {
          zipLink.hidden = false;
          zipLink.href = `${base}&as=zip`;
          zipLink.textContent = "下载社区 ZIP";
        }
      } else {
        link.hidden = true;
        if (zipLink) zipLink.hidden = true;
      }
    }
  }

  function renderLocalized() {
    const select = el("voice-localized-bank");
    const pill = el("voice-localized-pill");
    const hint = el("voice-localized-hint");
    if (!select) return;
    const taskLink = el("voice-translation-task");
    if (!state.localizedBanks.length) {
      select.innerHTML = '<option value="">没有可用的本地化语音表</option>';
      select.disabled = true;
      setPill(pill, "不可用", "warn");
      hint.textContent = state.localizedError || "先在“工作台”选择官方 FMOD 工程。";
      if (taskLink) taskLink.hidden = true;
      return;
    }
    select.disabled = false;
    const projectBank = state.current?.lines?.find(
      (line) => line.source?.localized,
    )?.source?.bank;
    const preferred =
      state.localizedInfo?.bank ||
      select.value ||
      projectBank ||
      (state.localizedBanks.some((bank) => bank.id === "_crew_dialogs_ground_en_us")
        ? "_crew_dialogs_ground_en_us"
        : state.localizedBanks[0].id);
    select.innerHTML = state.localizedBanks
      .map(
        (bank) =>
          `<option value="${escape(bank.id)}">${escape(bank.label)} · ${bank.stream_count} 个流名</option>`,
      )
      .join("");
    if (state.localizedBanks.some((bank) => bank.id === preferred)) {
      select.value = preferred;
    }
    if (taskLink) {
      const missingOnly = el("voice-translation-missing")?.checked;
      taskLink.hidden = !select.value;
      taskLink.href = select.value
        ? `/api/localized/translation-task?bank=${encodeURIComponent(select.value)}` +
          (missingOnly ? "&missing=1" : "")
        : "#";
    }
    const info = state.localizedInfo;
    if (info?.bank === select.value) {
      setPill(pill, `${info.streams.length} 条`, info.gameOnly ? "warn" : "ok");
      hint.textContent =
        `${info.label}：游戏 bank 共 ${info.streams.length} 个流名` +
        `（官方工程 ${select.selectedOptions[0]?.textContent || ""}）。` +
        (info.gameOnly
          ? `其中 ${info.gameOnly} 条是官方工程没有的新增流，构建时会从游戏 bank 保留原音。`
          : "官方工程与游戏 bank 的流名一致。") +
        coverageHint(select.value);
      return;
    }
    const bank = state.localizedBanks.find((item) => item.id === select.value);
    setPill(pill, "可加入", "subtle");
    hint.textContent = bank
      ? `${bank.label}：官方工程 ${bank.file_count} 个文件 / ${bank.stream_count} 个流名。点“刷新语音表”读取游戏 bank 中新增的原音。` +
        coverageHint(select.value)
      : "";
  }

  const SCOPE_ORDER = ["common", "ground", "naval"];
  const SCOPE_NAMES = { common: "通用", ground: "陆战", naval: "海战" };

  function coverageRow(bank) {
    return (state.coverage?.banks || []).find((row) => row.bank === bank) || null;
  }

  function coverageHint(bank) {
    const row = coverageRow(bank);
    if (!row) return "";
    if (!row.has_game_bank) return " · 游戏目录里没有这张 bank，无法统计中文覆盖。";
    if (!row.zh_banks?.length) {
      return ` · 没有官方中文表，${row.zh_missing} 条全部需要翻译后用 GPT-SoVITS 合成。`;
    }
    if (!row.zh_missing) return ` · 官方中文原音已覆盖 ${row.zh_cover}/${row.game} 条，无需补录。`;
    return (
      ` · 官方中文原音覆盖 ${row.zh_cover}/${row.game} 条，` +
      `剩余 ${row.zh_missing} 条需要翻译/合成。`
    );
  }

  function renderCoverage() {
    const wrap = el("voice-coverage");
    const summary = el("voice-coverage-summary");
    if (!wrap) return;
    if (state.coverageError) {
      wrap.hidden = true;
      if (summary) summary.textContent = state.coverageError;
      return;
    }
    if (!state.coverage) {
      wrap.hidden = true;
      return;
    }
    const rows = state.coverage.banks || [];
    const extras = state.coverage.extra_game_banks || [];
    const byScope = {};
    for (const row of rows) {
      const bucket = (byScope[row.scope] ||= []);
      bucket.push(row);
    }
    const scopeParts = SCOPE_ORDER.filter((scope) => byScope[scope]?.length).map((scope) => {
      const items = byScope[scope];
      const missing = items.reduce((sum, row) => sum + Number(row.zh_missing || 0), 0);
      const noZh = items.filter((row) => !row.zh_banks?.length).length;
      return `${SCOPE_NAMES[scope]} ${items.length} 张、缺中文 ${missing} 条` + (noZh ? `（${noZh} 张无中文表）` : "");
    });
    if (summary) {
      summary.textContent = state.coverage.has_game === false
        ? "没有找到游戏语音表。请先在“工作台”选择战雷目录，再统计中文覆盖。"
        : `共 ${rows.length} 张语音表：` +
          scopeParts.join("；") +
          (extras.length
            ? `。游戏里另有 ${extras.length} 张官方工程未收录的其他语言表，当前不能生成。`
            : "。");
    }
    const selected = el("voice-localized-bank")?.value || "";
    const head =
      '<div class="coverage-row coverage-head">' +
      "<span>语音表</span><span>工程</span><span>游戏</span><span>新增</span><span>中文原音</span></div>";
    const groups = SCOPE_ORDER.filter((scope) => byScope[scope]?.length)
      .map((scope) => {
        const items = byScope[scope]
          .map((row) => {
            const missing = Number(row.zh_missing || 0);
            const ratio = row.game ? Number(row.zh_cover || 0) / row.game : 0;
            const zhClass = !row.zh_banks?.length ? "bad" : missing === 0 ? "ok" : ratio >= 0.95 ? "warn" : "bad";
            const zhText = !row.has_game_bank
              ? "找不到 bank"
              : row.zh_banks?.length
                ? `${row.zh_cover}/${row.game}${missing ? ` · 缺 ${missing}` : ""}`
                : `无中文表 · 缺 ${missing}`;
            return (
              `<button type="button" class="coverage-row${row.bank === selected ? " active" : ""}" data-bank="${escape(row.bank)}">` +
              `<span class="coverage-label">${escape(row.label)}</span>` +
              `<span>${row.kit}</span>` +
              `<span>${row.has_game_bank ? row.game : "—"}</span>` +
              `<span>${row.game_only || 0}</span>` +
              `<span class="coverage-zh ${zhClass}">${escape(zhText)}</span>` +
              "</button>"
            );
          })
          .join("");
        return `<div class="coverage-scope">${SCOPE_NAMES[scope]}</div>${items}`;
      })
      .join("");
    wrap.innerHTML = head + groups;
    wrap.hidden = false;
  }

  function markCoverageSelection() {
    const selected = el("voice-localized-bank")?.value || "";
    document.querySelectorAll("#voice-coverage [data-bank]").forEach((node) => {
      node.classList.toggle("active", node.dataset.bank === selected);
    });
  }

  async function loadCoverage() {
    const button = el("voice-coverage-refresh");
    const summary = el("voice-coverage-summary");
    state.coverageError = "";
    if (button) {
      button.disabled = true;
      button.textContent = "统计中…";
    }
    if (summary) summary.textContent = "正在读取每张语音表的流名，首次约需半分钟…";
    try {
      state.coverage = await apiCall("/api/localized/coverage");
    } catch (error) {
      state.coverage = null;
      state.coverageError = error.message;
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "统计中文覆盖";
      }
      renderCoverage();
      renderLocalized();
    }
  }

  function fillLocalizedSources() {
    const select = el("voice-localized-source");
    if (!select) return;
    const previous = select.value;
    const others = state.projects.filter((project) => project.id !== state.current?.id);
    select.innerHTML =
      '<option value="">不复制，只加入空台词</option>' +
      others
        .map(
          (project) =>
            `<option value="${escape(project.id)}">${escape(project.name)} · ${project.line_count} 句</option>`,
        )
        .join("");
    const stored = localStorage.getItem("wt.voice.localizedSource") || "";
    const wanted = previous || stored;
    if (others.some((project) => project.id === wanted)) select.value = wanted;
    select.disabled = !others.length;
  }

  async function loadLocalizedBanks() {
    state.localizedError = "";
    state.localizedInfo = null;
    try {
      const data = await apiCall("/api/localized/banks");
      state.localizedBanks = data.banks || [];
    } catch (error) {
      state.localizedBanks = [];
      state.localizedError = error.message;
    }
    renderLocalized();
    fillLocalizedSources();
  }

  async function loadLocalizedInfo() {
    const select = el("voice-localized-bank");
    const pill = el("voice-localized-pill");
    const hint = el("voice-localized-hint");
    const bank = select?.value;
    if (!bank) return;
    setPill(pill, "读取中…", "warn");
    if (hint) hint.textContent = "正在读取游戏 bank 的流名，首次会慢几秒…";
    try {
      const data = await apiCall(
        `/api/localized/streams?bank=${encodeURIComponent(bank)}`,
      );
      const item = state.localizedBanks.find((row) => row.id === bank);
      state.localizedInfo = {
        bank,
        label: item?.label || bank,
        streams: data.streams || [],
        gameOnly: Number(data.game_only || 0),
      };
      renderLocalized();
    } catch (error) {
      setPill(pill, "读取失败", "bad");
      if (hint) hint.textContent = error.message;
    }
  }

  async function importLocalized(copySource) {
    if (!state.current) {
      toastMessage("先建一个声音项目", "bad");
      return;
    }
    const bank = el("voice-localized-bank")?.value;
    const source = copySource ? el("voice-localized-source")?.value || "" : "";
    if (!bank) {
      toastMessage("先选择一张本地化语音表", "bad");
      return;
    }
    if (copySource && !source) {
      toastMessage("先选择一个已有的中文项目", "bad");
      return;
    }
    const status = el("voice-localized-status");
    const addButton = el("voice-localized-add");
    const copyButton = el("voice-localized-copy");
    addButton.disabled = true;
    copyButton.disabled = true;
    if (status) status.textContent = "正在读取语音表并加入台词…";
    try {
      const data = await apiCall("/api/voice/import-localized", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          bank,
          source_project: source,
        }),
      });
      state.current = data.project;
      const index = state.projects.findIndex((item) => item.id === data.project.id);
      if (index >= 0) {
        state.projects[index] = {
          ...state.projects[index],
          line_count: (data.project.lines || []).length,
          ready_count: (data.project.lines || []).filter((line) => line.audio).length,
        };
        renderProjects();
      }
      renderProject();
      const missing = data.missing || [];
      const parts = [`新增 ${data.added} 条`];
      if (data.skipped) parts.push(`已有 ${data.skipped} 条`);
      if (copySource) {
        parts.push(`复用文本 ${data.text_copied ?? 0} 条`);
        parts.push(`复用音频 ${data.audio_copied ?? 0} 条`);
        parts.push(`待翻译 ${missing.length} 条`);
      }
      if (status) {
        status.textContent =
          parts.join("，") +
          (missing.length
            ? `。先听原音、填好中文再合成：${missing.slice(0, 6).join("、")}${missing.length > 6 ? "…" : ""}`
            : "。");
      }
      if (copySource && missing.length) {
        el("voice-only-missing").checked = true;
        renderLines();
      }
      toastMessage(
        copySource
          ? `已加入 ${data.added} 条，待翻译 ${missing.length} 条`
          : `已加入 ${data.added} 条台词`,
        missing.length ? "warn" : "ok",
      );
    } catch (error) {
      if (status) status.textContent = error.message;
      toastMessage(error.message, "bad");
    } finally {
      addButton.disabled = false;
      copyButton.disabled = false;
    }
  }

  async function copyOfficialAudio() {
    if (!state.current) {
      toastMessage("先建一个声音项目", "bad");
      return;
    }
    const bank = el("voice-localized-bank")?.value;
    if (!bank) {
      toastMessage("先选择一张本地化语音表", "bad");
      return;
    }
    const status = el("voice-localized-status");
    const addButton = el("voice-localized-add");
    const copyButton = el("voice-localized-copy");
    const officialButton = el("voice-localized-official");
    addButton.disabled = true;
    copyButton.disabled = true;
    officialButton.disabled = true;
    if (status) status.textContent = "正在读取游戏自带的中文语音表，并复制同名流…";
    try {
      const data = await apiCall("/api/voice/copy-official", {
        method: "POST",
        body: JSON.stringify({ project: state.current.id, bank }),
      });
      state.current = data.project;
      const index = state.projects.findIndex((item) => item.id === data.project.id);
      if (index >= 0) {
        state.projects[index] = {
          ...state.projects[index],
          line_count: (data.project.lines || []).length,
          ready_count: (data.project.lines || []).filter((line) => line.audio).length,
        };
        renderProjects();
      }
      renderProject();
      const missing = data.missing || [];
      const sources = (data.source_banks || [data.source_bank]).filter(Boolean);
      const extra = Number(data.supplemented || 0);
      if (status) {
        status.textContent =
          `已从 ${sources.join(" + ")} 复制 ${data.copied} 条同名中文原音` +
          (extra ? `（其中 ${extra} 条来自补充表）` : "") +
          `；剩余 ${missing.length} 条未完成` +
          (missing.length
            ? `：${missing.slice(0, 6).join("、")}${missing.length > 6 ? "…" : ""}`
            : "。");
      }
      if (missing.length) {
        el("voice-only-missing").checked = true;
        renderLines();
      }
      toastMessage(
        `复用官方中文 ${data.copied} 条，剩余 ${missing.length} 条`,
        missing.length ? "warn" : "ok",
      );
    } catch (error) {
      if (status) status.textContent = error.message;
      toastMessage(error.message, "bad");
    } finally {
      addButton.disabled = false;
      copyButton.disabled = false;
      officialButton.disabled = false;
    }
  }

  function renderReferenceTexts() {
    const box = el("voice-ref-text-list");
    if (!box) return;
    const project = state.current;
    const files = project?.reference?.files || [];
    const texts = project?.reference?.texts || {};
    if (!files.length) {
      box.innerHTML = '<span class="muted small">还没有参考音频。</span>';
      return;
    }
    box.innerHTML = files
      .map(
        (name) =>
          `<div class="voice-ref-text-row">` +
          `<div class="mono small">${escape(name)}</div>` +
          `<textarea class="input textarea" rows="2" data-ref-name="${escape(name)}" ` +
          `placeholder="这段音频里说的文字">${escape(texts[name] || "")}</textarea>` +
          `</div>`,
      )
      .join("");
  }

  function renderModel() {
    const project = state.current;
    const model = project?.model || {};
    const pill = el("voice-model-pill");
    const detail = el("voice-model-detail");
    const button = el("voice-train-btn");
    const hint = el("voice-train-hint");
    const files = project?.reference?.files || [];
    const trained = model.mode === "fine_tuned" && model.state === "ready";
    setPill(pill, trained ? "已训练" : "零样本", trained ? "ok" : "subtle");
    const toggle = el("voice-model-toggle");
    if (toggle) {
      const hasWeights = Boolean(model.sovits_path && model.gpt_path);
      toggle.hidden = !hasWeights;
      toggle.textContent = trained ? "换回零样本" : "使用训练模型";
    }
    if (detail) {
      detail.textContent = trained
        ? `${model.detail || "微调完成"}${model.trained_at ? ` · ${formatLocal(model.trained_at)}` : ""}`
        : "当前使用参考音频直接克隆，不需要训练。";
    }
    const ready = files.length >= 3;
    if (button) {
      button.disabled = !ready;
      button.textContent = trained ? "重新训练" : "训练专属音色";
    }
    if (hint) {
      hint.textContent = ready
        ? ""
        : `至少需要 3 段参考音频，当前 ${files.length} 段`;
    }
  }

  function renderTakeRow(line) {
    const takes = Array.isArray(line.takes) ? line.takes : [];
    if (takes.length < 2) return "";
    const selected = line.selected_take || takes[0]?.id || "";
    const items = takes
      .map((take, index) => {
        const active = take.id === selected;
        const seconds = take.duration_sec ? `${Number(take.duration_sec).toFixed(2)}s` : "—";
        const ref = take.ref_file ? ` · 参考 ${take.ref_file}` : "";
        return (
          `<span class="take-item${active ? " current" : ""}">` +
          `<button class="take-chip" type="button" data-play-take="${escape(take.id)}" ` +
          `title="试听第 ${index + 1} 版${escape(ref)}">第 ${index + 1} 版 · ${seconds}</button>` +
          (active
            ? '<span class="take-current">当前</span>'
            : `<button class="take-pick" type="button" data-select-take="${escape(take.id)}">选用</button>`) +
          `</span>`
        );
      })
      .join("");
    return (
      `<div class="voice-takes">` +
      `<span class="voice-takes-label">候选版本</span>` +
      `<div class="take-list">${items}</div>` +
      `</div>`
    );
  }

  function tuningBadge(line) {
    if (line.text_source === "non_voice") return "";
    const tuning = line.tuning && typeof line.tuning === "object" ? line.tuning : {};
    const parts = [];
    const reference = String(tuning.reference || "");
    if (reference) {
      const files = state.current?.reference?.files || [];
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
    return `<span class="pill subtle take-tuning" title="${escape(title)}">${escape(parts.join(" · "))}</span>`;
  }

  function openTuning(card) {
    if (!state.current || !window.WTLineTuning) return;
    const line = (state.current.lines || []).find((item) => item.id === card.dataset.lineId);
    if (!line) return;
    const textarea = card.querySelector("[data-line-text]");
    const text = textarea ? textarea.value.trim() : line.text;
    const all = state.current.lines || [];
    const onlyMissing = Boolean(el("voice-only-missing")?.checked);
    const scope = (onlyMissing ? all.filter((item) => !item.audio) : all)
      .filter((item) => item.text_source !== "non_voice")
      .map((item) => item.id);
    window.WTLineTuning.open({
      project: state.current,
      line,
      text,
      scope,
      original:
        line.text_source === "original" || state.current.quick?.mode === "original",
    });
  }

  function renderLines() {
    const body = el("voice-lines-body");
    if (!body) return;
    const all = state.current?.lines || [];
    const count = el("voice-line-count");
    if (count) count.textContent = String(all.length);
    const onlyMissing = Boolean(el("voice-only-missing")?.checked);
    const lines = onlyMissing ? all.filter((line) => !line.audio) : all;
    if (!all.length) {
      body.innerHTML = '<p class="muted small">还没有台词。先在上面输入一句，或者从游戏素材里找事件名。</p>';
      updatePlayAll();
      return;
    }
    if (!lines.length) {
      body.innerHTML = '<p class="muted small">没有未完成的台词，取消勾选“只看未完成”可以查看全部。</p>';
      updatePlayAll();
      return;
    }
    body.innerHTML = lines
      .map((line) => {
        const duration = line.duration_sec ? `${Number(line.duration_sec).toFixed(2)}s` : "—";
        const source = line.source?.path
          ? `<div class="voice-line-source mono" title="${escape(line.source.path)}">源文件：${escape(line.source.file || line.source.path)}</div>`
          : '<div class="voice-line-source mono">手动台词（没有绑定游戏文件）</div>';
        const localized = line.source?.localized
          ? `<div class="voice-line-source mono">${escape(line.source.label || "本地化语音表")} · ${escape(line.source.game_only ? "游戏新增流" : "官方工程流")}${line.audio_source === "official_cn" ? " · 已复用官方中文原音" : ""}</div>`
          : "";
        const pending = !line.audio && !String(line.text || "").trim()
          ? '<span class="pill warn">待翻译</span>'
          : "";
        const error = line.error
          ? `<p class="hint small voice-line-error">${escape(line.error)}</p>`
          : "";
        return (
          `<article class="voice-line" data-line-id="${escape(line.id)}">` +
          `<div class="voice-line-top">` +
          `<span class="voice-line-event mono">${escape(line.event || "未命名事件")}</span>` +
          `<span class="pill ${statusTone(line)}">${statusLabel(line)}</span>` +
          tuningBadge(line) +
          pending +
          `<span class="muted small">${duration}</span>` +
          `</div>` +
          `<textarea class="input textarea voice-line-text" rows="2" data-line-text>${escape(line.text)}</textarea>` +
          source +
          localized +
          renderTakeRow(line) +
          `<div class="voice-line-actions">` +
          `<button class="btn ghost small" type="button" data-save-line>保存文本</button>` +
          `<button class="btn small" type="button" data-synth-line>${takeCount() > 1 ? `合成 ${takeCount()} 版` : "合成"}</button>` +
          `<button class="btn ghost small" type="button" data-play-line ${line.audio ? "" : "disabled"}>试听</button>` +
          `<button class="btn ghost small" type="button" data-play-source ${line.source?.path ? "" : "disabled"}>原音</button>` +
          `<button class="btn ghost small" type="button" data-import-take title="用现成 WAV 替换这一句">导入音频</button>` +
          (line.text_source === "non_voice"
            ? ""
            : `<button class="btn ghost small" type="button" data-tune-line title="调整这一句的语气参考、语速和音量">${line.text_source === "original" ? "音量" : "语气/音量"}</button>`) +
          `<button class="btn ghost small" type="button" data-delete-line>删除</button>` +
          `</div>` +
          error +
          `</article>`
        );
      })
      .join("");
    const current = playback.currentId || playback.lastLine?.id;
    if (current) markVoicePlaying(current);
    updatePlayAll();
  }

  function renderProgress(job) {
    const wrap = el("voice-progress");
    const fill = el("voice-progress-fill");
    const text = el("voice-progress-text");
    if (!job) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    const percent = Number(job.percent || 0);
    fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    const elapsed = job.elapsed != null ? ` · 已用 ${formatEta(job.elapsed)}` : "";
    text.textContent = `${job.message || "处理中…"}${job.percent == null ? "" : ` · ${percent.toFixed(0)}%`}${elapsed}`;
  }

  function renderTrainProgress(job) {
    const wrap = el("voice-train-progress");
    const fill = el("voice-train-fill");
    const text = el("voice-train-text");
    if (!wrap) return;
    if (!job) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    const percent = Number(job.percent || 0);
    fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    const elapsed = job.elapsed != null ? ` · 已用 ${formatEta(job.elapsed)}` : "";
    text.textContent = `${job.message || "训练中…"}${job.percent == null ? "" : ` · ${percent.toFixed(0)}%`}${elapsed}`;
    el("voice-train-cancel").hidden = !job.active;
    el("voice-train-btn").disabled = Boolean(job.active);
  }

  function renderBuildProgress(job) {
    const wrap = el("voice-build-progress");
    const fill = el("voice-build-fill");
    const text = el("voice-build-text");
    if (!wrap) return;
    if (!job) {
      wrap.hidden = true;
      el("voice-build-cancel").hidden = true;
      renderBuild();
      return;
    }
    wrap.hidden = false;
    const percent = Number(job.percent || 0);
    fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    const elapsed = job.elapsed != null ? ` · 已用 ${formatEta(job.elapsed)}` : "";
    text.textContent = `${job.message || "构建中…"}${job.percent == null ? "" : ` · ${percent.toFixed(0)}%`}${elapsed}`;
    el("voice-build-cancel").hidden = !job.active;
    el("voice-build-btn").disabled = Boolean(job.active);
  }

  function startJobPolling() {
    clearInterval(state.pollTimer);
    const tick = async () => {
      try {
        const data = await apiCall("/api/jobs/current");
        const job = data.job;
        if (!job || !["synthesize", "train", "build"].includes(job.kind)) {
          state.job = null;
          renderProgress(null);
          renderTrainProgress(null);
          renderBuildProgress(null);
          clearInterval(state.pollTimer);
          return;
        }
        state.job = job;
        if (job.kind === "train") renderTrainProgress(job);
        else if (job.kind === "build") renderBuildProgress(job);
        else renderProgress(job);
        if (!job.active) {
          clearInterval(state.pollTimer);
          const failed = job.state === "error";
          if (failed) toastMessage(job.error || "任务失败", "bad");
          else if (job.state === "cancelled") toastMessage("任务已取消");
          else {
            const message = {
              train: "训练完成，已切换新音色",
              build: "语音包已生成，可以下载",
              synthesize: job.message || "合成完成",
            }[job.kind];
            toastMessage(message);
          }
          renderTrainProgress(job.kind === "train" ? job : null);
          renderBuildProgress(job.kind === "build" ? job : null);
          await selectProject(state.current?.id);
          if (job.kind === "train") await loadEngine();
        }
      } catch {
        /* 轮询失败时保持当前状态，下一轮再试 */
      }
    };
    tick();
    state.pollTimer = setInterval(tick, 1500);
  }

  async function loadEngine() {
    try {
      const data = await apiCall("/api/voice/engine");
      state.engine = data.engine;
      renderEngine();
    } catch {
      state.engine = { available: false, detail: "检测失败" };
      renderEngine();
    }
  }

  const VOICE_TABS = ["lines", "voice", "localize", "build"];

  function showVoiceTab(name) {
    if (!VOICE_TABS.includes(name)) return;
    for (const button of document.querySelectorAll("#voice-tabs [data-voice-tab]")) {
      const active = button.dataset.voiceTab === name;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    }
    for (const panel of document.querySelectorAll("#page-voices [data-voice-panel]")) {
      panel.hidden = panel.dataset.voicePanel !== name;
    }
  }

  function bindVoiceTabs() {
    el("voice-tabs")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-voice-tab]");
      if (button) showVoiceTab(button.dataset.voiceTab);
    });
  }

  async function loadProjects(preferred = "") {
    const data = await apiCall("/api/voice/projects");
    state.projects = data.projects || [];
    renderProjects();
    const available = new Set(state.projects.map((project) => project.id));
    let target =
      preferred ||
      state.current?.id ||
      localStorage.getItem("wt.voice.project") ||
      state.projects[0]?.id ||
      "";
    // 上一次记住的项目可能已经被删除或换过数据目录，回退到第一个可用项目。
    if (target && !available.has(target)) target = state.projects[0]?.id || "";
    if (target) await selectProject(target);
  }

  async function selectProject(projectId) {
    if (!projectId) return;
    stopVoicePlayback(false, { reset: true });
    const data = await apiCall(`/api/voice/project?project=${encodeURIComponent(projectId)}`);
    state.current = data.project;
    state.build = data.build || null;
    state.training = data.training || null;
    localStorage.setItem("wt.voice.project", projectId);
    renderProjects();
    el("voice-project-select").value = projectId;
    renderProject();
  }

  async function createProject() {
    const name = el("voice-new-name").value.trim();
    if (!name) {
      toastMessage("先给项目起个名字", "bad");
      return;
    }
    try {
      const data = await apiCall("/api/voice/projects", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      el("voice-new-name").value = "";
      await loadProjects(data.project.id);
      toastMessage("声音项目已创建");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function importReference() {
    if (!state.current) return;
    try {
      const chosen = await apiCall("/api/dialog/folder", {
        method: "POST",
        body: JSON.stringify({ title: "选择参考音频文件夹" }),
      });
      if (chosen.cancelled || !chosen.path) return;
      const data = await apiCall("/api/voice/reference", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          path: chosen.path,
          prompt_text: el("voice-prompt").value,
          prompt_lang: el("voice-prompt-lang").value,
        }),
      });
      state.current = data.project;
      renderProject();
      toastMessage(`已导入 ${data.copied} 段参考音频`);
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function savePrompt() {
    if (!state.current) return;
    try {
      const data = await apiCall("/api/voice/prompt", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          prompt_text: el("voice-prompt").value,
          prompt_lang: el("voice-prompt-lang").value,
          prompt_file: el("voice-prompt-file").value,
        }),
      });
      state.current = data.project;
      renderProject();
      toastMessage("参考信息已保存");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function detectPromptText() {
    if (!state.current) return;
    if (!(state.current.reference?.files || []).length) {
      toastMessage("先导入参考音频", "bad");
      return;
    }
    const button = el("voice-detect-btn");
    button.disabled = true;
    button.textContent = "识别中…";
    try {
      const data = await apiCall("/api/voice/transcribe", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          file: el("voice-prompt-file").value,
          lang: el("voice-prompt-lang").value,
        }),
      });
      state.current = data.project;
      renderProject();
      toastMessage("已识别参考文字，请核对后再合成");
    } catch (error) {
      toastMessage(error.message, "bad");
    } finally {
      button.disabled = false;
      button.textContent = "自动识别文字";
    }
  }

  async function saveReferenceTexts() {
    if (!state.current) return;
    const texts = {};
    el("voice-ref-text-list")
      .querySelectorAll("[data-ref-name]")
      .forEach((node) => {
        texts[node.dataset.refName] = node.value;
      });
    try {
      const data = await apiCall("/api/voice/reference-texts", {
        method: "POST",
        body: JSON.stringify({ project: state.current.id, texts }),
      });
      state.current = data.project;
      renderProject();
      toastMessage("参考文字已保存");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function toggleModel() {
    if (!state.current) return;
    const model = state.current.model || {};
    const mode = model.mode === "fine_tuned" ? "zero_shot" : "fine_tuned";
    try {
      const data = await apiCall("/api/voice/model", {
        method: "POST",
        body: JSON.stringify({ project: state.current.id, mode }),
      });
      state.current = data.project;
      renderProject();
      toastMessage(mode === "fine_tuned" ? "已切换回训练模型" : "已切换回零样本");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function trainModel() {
    if (!state.current) return;
    const files = state.current.reference?.files?.length || 0;
    if (files < 3) {
      toastMessage("至少需要 3 段参考音频", "bad");
      return;
    }
    if (!confirm("训练会用这些参考音频微调音色，并清空已合成的音频。继续？")) return;
    try {
      const data = await apiCall("/api/voice/train", {
        method: "POST",
        body: JSON.stringify({ project: state.current.id }),
      });
      state.job = data.job;
      renderTrainProgress(data.job);
      startJobPolling();
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function addLine() {
    if (!state.current) return;
    const text = el("voice-new-text").value.trim();
    if (!text) {
      toastMessage("先输入台词", "bad");
      return;
    }
    try {
      const data = await apiCall("/api/voice/lines", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          text,
          event: el("voice-new-event").value.trim(),
        }),
      });
      state.current = data.project;
      el("voice-new-text").value = "";
      el("voice-new-event").value = "";
      renderProject();
      toastMessage("台词已添加");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function saveLine(card) {
    const text = card.querySelector("[data-line-text]").value.trim();
    if (!text) {
      toastMessage("台词不能为空", "bad");
      return;
    }
    try {
      const data = await apiCall("/api/voice/line/update", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          line: card.dataset.lineId,
          text,
        }),
      });
      state.current = data.project;
      renderProject();
      toastMessage("文本已保存，可以重新合成");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  function takeCount() {
    const value = Number(el("voice-take-count")?.value || 1);
    if (!Number.isFinite(value)) return 1;
    return Math.max(1, Math.min(4, Math.round(value)));
  }

  function syncTakeCount() {
    const count = takeCount();
    const button = el("voice-batch-btn");
    if (button) button.textContent = count > 1 ? `合成未完成 · ${count} 版` : "合成未完成";
    const hint = el("voice-take-hint");
    if (hint) {
      hint.textContent =
        count > 1
          ? "多版只换随机种子，音色稳定，但差异不大。建议只给个别句子加版本。"
          : "单句不满意时，把版本数调到 2–4 再合成，逐个试听选用。";
    }
    document.querySelectorAll("[data-synth-line]").forEach((node) => {
      node.textContent = count > 1 ? `合成 ${count} 版` : "合成";
    });
    try {
      localStorage.setItem("wt.voice.takes.v2", String(count));
    } catch {
      /* 本地存储不可用时忽略 */
    }
  }

  async function synthLines(lines, force) {
    if (!state.current) return;
    stopVoicePlayback(false);
    try {
      const data = await apiCall("/api/voice/synthesize", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          lines,
          force,
          versions: takeCount(),
          text_lang: el("voice-prompt-lang").value,
        }),
      });
      state.job = data.job;
      renderProgress(data.job);
      startJobPolling();
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function buildPackage() {
    if (!state.current) return;
    if (!state.build?.ready) {
      toastMessage(state.build?.reason || "还不能构建语音包", "bad");
      return;
    }
    if (!confirm("构建会用 FMOD 重新生成涉及的 bank，可能需要几分钟。继续？")) return;
    try {
      const data = await apiCall("/api/voice/build", {
        method: "POST",
        body: JSON.stringify({ project: state.current.id }),
      });
      state.job = data.job;
      renderBuildProgress(data.job);
      startJobPolling();
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function deleteLine(card) {
    if (!confirm("删除这句台词和已合成的音频？")) return;
    try {
      const data = await apiCall("/api/voice/line/delete", {
        method: "POST",
        body: JSON.stringify({ project: state.current.id, line: card.dataset.lineId }),
      });
      state.current = data.project;
      renderProject();
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  function findLine(card) {
    return (state.current?.lines || []).find((item) => item.id === card.dataset.lineId);
  }

  function playableLines() {
    return (state.current?.lines || []).filter((line) => Boolean(line.audio));
  }

  function voiceAudioIndex(lineId) {
    return playableLines().findIndex((line) => line.id === lineId);
  }

  function voiceNextId(lineId) {
    const lines = playableLines();
    if (!lines.length) return "";
    const index = lines.findIndex((line) => line.id === lineId);
    if (index < 0) return lines[0].id;
    return lines[(index + 1) % lines.length]?.id || lines[0].id;
  }

  function clearVoicePlayingMark() {
    document
      .querySelectorAll(".voice-line.playing")
      .forEach((node) => node.classList.remove("playing"));
  }

  function markVoicePlaying(lineId) {
    clearVoicePlayingMark();
    const card = document.querySelector(
      `.voice-line[data-line-id="${CSS.escape(lineId)}"]`,
    );
    if (card) card.classList.add("playing");
  }

  function updatePlayAll() {
    const button = el("voice-play-all");
    const total = playableLines().length;
    if (button) {
      if (playback.active) {
        const queueTotal = playback.queue.length;
        button.disabled = false;
        button.textContent = queueTotal
          ? `停止试听 ${Math.min(playback.index, queueTotal)}/${queueTotal}`
          : "停止试听";
      } else {
        button.disabled = !total;
        button.textContent = total ? `一键试听全部（${total} 句）` : "一键试听全部";
      }
    }
    const cont = el("voice-play-continue");
    if (cont) {
      const index = playback.resumeId ? voiceAudioIndex(playback.resumeId) : -1;
      cont.disabled = !total || index < 0 || playback.active;
      cont.title =
        index >= 0 ? `从第 ${index + 1} 句继续` : "试听或停止后，可从上次位置继续";
    }
    const loop = el("voice-loop-audition");
    if (loop) loop.checked = playback.loop;
    const locate = el("voice-locate-current");
    if (locate) {
      const lineId = playback.currentId || playback.lastLine?.id || "";
      locate.disabled =
        !lineId || !(state.current?.lines || []).some((line) => line.id === lineId);
    }
  }

  function stopVoicePlayback(clearLabel = true, options = {}) {
    const interrupted = playback.currentId || playback.lastLine?.id || "";
    if (interrupted) playback.resumeId = interrupted;
    const lastLine = playback.lastLine;
    playback.active = false;
    playback.queue = [];
    playback.index = 0;
    playback.currentId = "";
    const player = el("voice-player");
    if (player) {
      player.pause();
      player.removeAttribute("src");
      try {
        player.load();
      } catch {
        /* 忽略播放器重载异常 */
      }
    }
    if (options.reset) {
      playback.lastLine = null;
      playback.resumeId = "";
      clearVoicePlayingMark();
      if (clearLabel && el("voice-player-label")) {
        el("voice-player-label").textContent = "选择一句合成后播放";
      }
    } else if (lastLine) {
      markVoicePlaying(lastLine.id);
      if (clearLabel && el("voice-player-label")) {
        el("voice-player-label").textContent =
          `已停止 · ${lastLine.text || lastLine.event || "台词"}`.slice(0, 42);
      }
    } else if (clearLabel && el("voice-player-label")) {
      el("voice-player-label").textContent = "选择一句合成后播放";
    }
    updatePlayAll();
  }

  function voicePlayNext() {
    if (!playback.active) return;
    const player = el("voice-player");
    if (!player) return;
    if (playback.index >= playback.queue.length) {
      if (playback.loop && playback.queue.length > 1) {
        playback.index = 0;
        voicePlayNext();
        return;
      }
      const total = playback.queue.length;
      const lastLine = playback.lastLine;
      playback.active = false;
      playback.queue = [];
      playback.index = 0;
      playback.currentId = "";
      playback.resumeId = playableLines()[0]?.id || "";
      player.pause();
      player.removeAttribute("src");
      try {
        player.load();
      } catch {
        /* 忽略 */
      }
      if (lastLine) {
        markVoicePlaying(lastLine.id);
        el("voice-player-label").textContent =
          `试听完成（${total} 句）· 点“继续听”从头循环`;
      }
      updatePlayAll();
      return;
    }
    const line = playback.queue[playback.index];
    playback.index += 1;
    playback.currentId = line.id;
    playback.lastLine = line;
    markVoicePlaying(line.id);
    player.src =
      `/api/voice/audio?project=${encodeURIComponent(state.current.id)}` +
      `&line=${encodeURIComponent(line.id)}`;
    el("voice-player-label").textContent =
      `连续试听 ${playback.index}/${playback.queue.length}` +
      `${playback.loop ? " · 循环" : ""} · ` +
      `${line.text || line.event || "台词"}`.slice(0, 40);
    updatePlayAll();
    player
      .play()
      .then(() => {
        playback.failures = 0;
      })
      .catch(() => skipVoiceLine(line.id));
  }

  function skipVoiceLine(lineId) {
    if (!playback.active || !lineId || playback.currentId !== lineId) return;
    playback.currentId = "";
    playback.failures += 1;
    if (playback.failures >= 3) {
      stopVoicePlayback(false);
      el("voice-player-label").textContent = "连续 3 句播放失败，已停止试听";
      return;
    }
    toastMessage("这一句播放失败，已跳过", "bad");
    setTimeout(() => {
      if (playback.active) voicePlayNext();
    }, 180);
  }

  function startVoicePlayback(start = 0) {
    const lines = playableLines();
    if (!lines.length) {
      toastMessage("还没有已合成的台词，先合成再试听", "bad");
      updatePlayAll();
      return;
    }
    stopVoicePlayback(false);
    const player = el("voice-player");
    if (player) {
      player.pause();
      player.removeAttribute("src");
      try {
        player.load();
      } catch {
        /* 忽略 */
      }
    }
    playback.queue = lines.slice();
    playback.index = Math.max(0, Math.min(Number(start) || 0, lines.length - 1));
    playback.active = true;
    playback.failures = 0;
    voicePlayNext();
  }

  function continueVoicePlayback() {
    if (playback.active) return;
    const lines = playableLines();
    if (!lines.length) {
      toastMessage("还没有已合成的台词，先合成再试听", "bad");
      return;
    }
    let start = lines.findIndex((line) => line.id === playback.resumeId);
    if (start < 0) start = 0;
    startVoicePlayback(start);
  }

  function locateCurrentVoiceLine() {
    const lineId = playback.currentId || playback.lastLine?.id || "";
    if (!lineId) return;
    let card = document.querySelector(
      `.voice-line[data-line-id="${CSS.escape(lineId)}"]`,
    );
    if (!card) {
      const onlyMissing = el("voice-only-missing");
      if (onlyMissing?.checked) {
        onlyMissing.checked = false;
        renderLines();
      }
      card = document.querySelector(
        `.voice-line[data-line-id="${CSS.escape(lineId)}"]`,
      );
    }
    if (!card) {
      toastMessage("这句在当前列表里找不到，取消筛选后再试", "bad");
      return;
    }
    card.scrollIntoView({ block: "center" });
    card.classList.remove("locate");
    void card.offsetWidth;
    card.classList.add("locate");
    setTimeout(() => card.classList.remove("locate"), 1200);
  }

  function toggleVoicePlayback() {
    if (playback.active) stopVoicePlayback();
    else startVoicePlayback(0);
  }

  function markSingleVoiceLine(line) {
    playback.lastLine = line;
    playback.currentId = line.id;
    playback.resumeId = line.id;
    markVoicePlaying(line.id);
    updatePlayAll();
  }

  function playLine(card) {
    const line = findLine(card);
    if (!line?.audio) return;
    stopVoicePlayback(false);
    markSingleVoiceLine(line);
    const player = el("voice-player");
    player.src =
      `/api/voice/audio?project=${encodeURIComponent(state.current.id)}` +
      `&line=${encodeURIComponent(line.id)}`;
    el("voice-player-label").textContent = line.text.slice(0, 40);
    player.play().catch(() => toastMessage("播放失败，请检查音频文件", "bad"));
  }

  function playTake(card, takeId) {
    const line = findLine(card);
    const takes = line?.takes || [];
    const index = takes.findIndex((take) => take.id === takeId);
    if (!line || index < 0) return;
    stopVoicePlayback(false);
    markSingleVoiceLine(line);
    const player = el("voice-player");
    player.src =
      `/api/voice/audio?project=${encodeURIComponent(state.current.id)}` +
      `&line=${encodeURIComponent(line.id)}` +
      `&take=${encodeURIComponent(takeId)}`;
    el("voice-player-label").textContent = `${line.text.slice(0, 32)} · 第 ${index + 1} 版`;
    player.play().catch(() => toastMessage("播放失败，请检查这一版的音频", "bad"));
  }

  async function selectTake(card, takeId) {
    if (!state.current) return;
    try {
      const data = await apiCall("/api/voice/take/select", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          line: card.dataset.lineId,
          take: takeId,
        }),
      });
      state.current = data.project;
      renderProject();
      const line = findLine(card);
      const index = (line?.takes || []).findIndex((take) => take.id === takeId);
      toastMessage(
        index >= 0 ? `已选用第 ${index + 1} 版，构建会使用这一版` : "已更换选用版本",
      );
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function importTake(card) {
    if (!state.current) return;
    try {
      const chosen = await apiCall("/api/dialog/files", {
        method: "POST",
        body: JSON.stringify({ title: "选择成品音频（WAV）" }),
      });
      const path = (chosen.paths || [])[0];
      if (!path) return;
      const data = await apiCall("/api/voice/take/import", {
        method: "POST",
        body: JSON.stringify({
          project: state.current.id,
          line: card.dataset.lineId,
          path,
        }),
      });
      state.current = data.project;
      renderLines();
      toastMessage("已导入音频，可在试听里确认");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  function playSource(card) {
    const line = (state.current?.lines || []).find((item) => item.id === card.dataset.lineId);
    if (!line?.source?.path) return;
    stopVoicePlayback(false);
    markSingleVoiceLine(line);
    const player = el("voice-player");
    player.src =
      `/api/audio/source?project=${encodeURIComponent(state.current.id)}` +
      `&line=${encodeURIComponent(line.id)}`;
    el("voice-player-label").textContent =
      `原音：${line.source.file || line.event || line.source.path}`;
    player.play().catch(() => toastMessage("播放失败，请检查原音文件", "bad"));
  }

  async function searchEvents() {
    const box = el("voice-event-results");
    if (!box) return;
    const query = el("voice-event-search").value.trim();
    if (!query) {
      box.innerHTML = '<p class="hint small">输入事件名再搜索。</p>';
      return;
    }
    box.innerHTML = '<p class="hint small">搜索中…</p>';
    try {
      const data = await apiCall(
        `/api/catalog/query?q=${encodeURIComponent(query)}&page_size=20&include_unassigned=1`,
      );
      const events = data.catalog?.events || [];
      if (!events.length) {
        box.innerHTML = '<p class="hint small">没有找到。先去“游戏素材”生成事件目录。</p>';
        return;
      }
      box.innerHTML = events
        .map((event) => {
          const asset = (event.assets || [])[0] || {};
          const path = asset.path || "（这个事件没有素材）";
          return (
            `<div class="voice-event-result">` +
            `<div><span class="voice-line-event mono">${escape(event.event)}</span>` +
            `<span class="pill subtle">${escape(event.bank || "unassigned")}</span></div>` +
            `<div class="mono small voice-event-path">${escape(path)}</div>` +
            `<button class="btn ghost small" type="button" data-add-event="${escape(event.id)}">添加这句</button>` +
            `</div>`
          );
        })
        .join("");
    } catch (error) {
      box.innerHTML = `<p class="hint small">${escape(error.message)}</p>`;
    }
  }

  async function importEvent(eventId) {
    if (!state.current) return;
    try {
      const data = await apiCall("/api/voice/import-events", {
        method: "POST",
        body: JSON.stringify({ project: state.current.id, ids: [eventId] }),
      });
      state.current = data.project;
      renderProject();
      toastMessage(data.added ? "已添加，源文件名已绑定" : "这个事件已经在台词表里");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  function formatBytes(value) {
    const size = Number(value || 0);
    if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(2)} GB`;
    if (size >= 1024 ** 2) return `${(size / 1024 ** 2).toFixed(0)} MB`;
    if (size >= 1024) return `${(size / 1024).toFixed(0)} KB`;
    return `${size} B`;
  }

  function componentProgressText(job) {
    const percent = Number(
      job.state === "verifying" || job.state === "extracting"
        ? job.phase_percent ?? 0
        : job.percent ?? 0,
    );
    if (job.state === "downloading") {
      const parts = [`已下载 ${formatBytes(job.received)} / ${formatBytes(job.total)}`];
      if (job.speed) parts.push(formatSpeed(job.speed));
      if (job.eta) parts.push(`剩余 ${formatEta(job.eta)}`);
      return parts.join(" · ");
    }
    if (job.state === "verifying") return `SHA-256 校验 ${percent.toFixed(1)}%`;
    if (job.state === "extracting") {
      return job.phase_percent == null ? "解压中…" : `解压 ${percent.toFixed(1)}%`;
    }
    return `进度 ${percent.toFixed(1)}%`;
  }

  async function cancelComponent() {
    try {
      await apiCall("/api/components/cancel", { method: "POST", body: "{}" });
      toastMessage("正在取消下载");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  async function installEngine() {
    try {
      const data = await apiCall("/api/components/download", {
        method: "POST",
        body: JSON.stringify({ id: "gpt-sovits", source: "auto" }),
      });
      toastMessage("开始下载 GPT-SoVITS");
      pollComponent(data.job);
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  function pollComponent(initial) {
    clearInterval(state.componentTimer);
    const box = el("voice-engine-box");
    const tick = async () => {
      try {
        const data = await apiCall("/api/components/job");
        const job = data.job || initial;
        if (!job) return;
        if (box) {
          const percent = Number(
            job.state === "verifying" || job.state === "extracting"
              ? job.phase_percent ?? 0
              : job.percent ?? 0,
          );
          const detail = componentProgressText(job);
          box.innerHTML =
            `<span class="pill warn">安装中</span>` +
            `<p class="hint">${escape(job.message || "准备中…")}</p>` +
            `<p class="hint small">${escape(detail)} · ${percent.toFixed(1)}%</p>` +
            `<div class="progress-bar"><span style="width:${Math.max(0, Math.min(100, percent))}%"></span></div>` +
            (job.active
              ? `<button class="btn ghost small" type="button" data-cancel-component="1">取消下载</button>`
              : "");
        }
        if (!["queued", "downloading", "verifying", "extracting"].includes(job.state)) {
          clearInterval(state.componentTimer);
          await loadEngine();
          if (job.state === "done") toastMessage("GPT-SoVITS 安装完成");
          else if (job.state === "error") toastMessage(job.error || "安装失败", "bad");
        }
      } catch {
        /* 下一轮再试 */
      }
    };
    tick();
    state.componentTimer = setInterval(tick, 2000);
  }

  function bindEvents() {
    bindVoiceTabs();
    el("voice-refresh-btn")?.addEventListener("click", () => {
      loadEngine();
      loadProjects().catch((error) => toastMessage(error.message, "bad"));
    });
    el("voice-create-btn")?.addEventListener("click", createProject);
    el("voice-project-select")?.addEventListener("change", (event) => {
      selectProject(event.target.value).catch((error) => toastMessage(error.message, "bad"));
    });
    el("voice-import-btn")?.addEventListener("click", importReference);
    el("voice-save-prompt")?.addEventListener("click", savePrompt);
    el("voice-detect-btn")?.addEventListener("click", detectPromptText);
    el("voice-save-texts")?.addEventListener("click", saveReferenceTexts);
    el("voice-train-btn")?.addEventListener("click", trainModel);
    el("voice-model-toggle")?.addEventListener("click", toggleModel);
    el("voice-train-cancel")?.addEventListener("click", () => {
      apiCall("/api/jobs/current/cancel", { method: "POST", body: "{}" }).catch(() => {});
    });
    el("voice-build-btn")?.addEventListener("click", buildPackage);
    el("voice-build-cancel")?.addEventListener("click", () => {
      apiCall("/api/jobs/current/cancel", { method: "POST", body: "{}" }).catch(() => {});
    });
    el("voice-localized-bank")?.addEventListener("change", () => {
      state.localizedInfo = null;
      markCoverageSelection();
      renderLocalized();
      loadLocalizedInfo();
    });
    el("voice-localized-refresh")?.addEventListener("click", async () => {
      await loadLocalizedBanks();
      await loadLocalizedInfo();
    });
    el("voice-coverage-refresh")?.addEventListener("click", loadCoverage);
    el("voice-translation-missing")?.addEventListener("change", renderLocalized);
    el("voice-coverage")?.addEventListener("click", (event) => {
      const row = event.target.closest("[data-bank]");
      const select = el("voice-localized-bank");
      if (!row || !select) return;
      select.value = row.dataset.bank;
      state.localizedInfo = null;
      markCoverageSelection();
      renderLocalized();
      loadLocalizedInfo();
    });
    el("voice-localized-add")?.addEventListener("click", () => importLocalized(false));
    el("voice-localized-copy")?.addEventListener("click", () => importLocalized(true));
    el("voice-localized-official")?.addEventListener("click", copyOfficialAudio);
    el("voice-localized-source")?.addEventListener("change", (event) => {
      localStorage.setItem("wt.voice.localizedSource", event.target.value);
    });
    el("voice-only-missing")?.addEventListener("change", renderLines);
    el("voice-play-all")?.addEventListener("click", toggleVoicePlayback);
    el("voice-play-continue")?.addEventListener("click", continueVoicePlayback);
    el("voice-locate-current")?.addEventListener("click", locateCurrentVoiceLine);
    el("voice-loop-audition")?.addEventListener("change", (event) => {
      playback.loop = Boolean(event.target.checked);
      localStorage.setItem("wt.voice.loop", playback.loop ? "1" : "0");
      toastMessage(playback.loop ? "已开启循环试听" : "已关闭循环试听");
    });
    const takeSelect = el("voice-take-count");
    if (takeSelect) {
      const stored = Number(localStorage.getItem("wt.voice.takes.v2") || "");
      if (stored >= 1 && stored <= 4) takeSelect.value = String(stored);
      takeSelect.addEventListener("change", syncTakeCount);
      syncTakeCount();
    }
    el("voice-add-line")?.addEventListener("click", addLine);
    el("voice-import-file")?.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file || !state.current) return;
      try {
        const text = await file.text();
        const data = await apiCall("/api/voice/import-texts", {
          method: "POST",
          body: JSON.stringify({ project: state.current.id, csv: text }),
        });
        state.current = data.project;
        renderProject();
        const errors = (data.errors || []).slice(0, 2).join("；");
        toastMessage(
          `已匹配 ${data.matched} 句，跳过 ${data.skipped} 句${errors ? `；${errors}` : ""}`,
          data.errors?.length ? "bad" : "ok",
        );
      } catch (error) {
        toastMessage(error.message, "bad");
      } finally {
        event.target.value = "";
      }
    });
    el("voice-event-search-btn")?.addEventListener("click", searchEvents);
    el("voice-event-search")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") searchEvents();
    });
    el("voice-event-results")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-add-event]");
      if (button) importEvent(button.dataset.addEvent);
    });
    el("voice-batch-btn")?.addEventListener("click", () => synthLines([], false));
    el("voice-job-cancel")?.addEventListener("click", () => {
      apiCall("/api/jobs/current/cancel", { method: "POST", body: "{}" }).catch(() => {});
    });
    el("voice-engine-box")?.addEventListener("click", (event) => {
      if (event.target.closest("[data-install-engine]")) installEngine();
      else if (event.target.closest("[data-cancel-component]")) cancelComponent();
    });
    el("voice-lines-body")?.addEventListener("click", (event) => {
      const card = event.target.closest(".voice-line");
      if (!card) return;
      const takePlay = event.target.closest("[data-play-take]");
      const takePick = event.target.closest("[data-select-take]");
      if (takePlay) playTake(card, takePlay.dataset.playTake);
      else if (takePick) selectTake(card, takePick.dataset.selectTake);
      else if (event.target.closest("[data-save-line]")) saveLine(card);
      else if (event.target.closest("[data-synth-line]")) synthLines([card.dataset.lineId], true);
      else if (event.target.closest("[data-play-line]")) playLine(card);
      else if (event.target.closest("[data-play-source]")) playSource(card);
      else if (event.target.closest("[data-import-take]")) importTake(card);
      else if (event.target.closest("[data-tune-line]")) openTuning(card);
      else if (event.target.closest("[data-delete-line]")) deleteLine(card);
    });
    el("voice-player")?.addEventListener("ended", () => {
      if (playback.active) voicePlayNext();
    });
    el("voice-player")?.addEventListener("error", () => {
      if (!el("voice-player").src) return;
      if (playback.active) {
        skipVoiceLine(playback.currentId);
        return;
      }
      toastMessage("播放失败：音频可能不存在", "bad");
    });
  }

  async function init() {
    if (state.initialized || !el("page-voices")) return;
    state.initialized = true;
    bindEvents();
    await loadEngine();
    showVoiceTab(state.engine?.available ? "lines" : "voice");
    try {
      await loadProjects();
    } catch (error) {
      toastMessage(error.message, "bad");
    }
    await loadLocalizedBanks();
    try {
      const data = await apiCall("/api/jobs/current");
      if (data.job?.active && ["synthesize", "train", "build"].includes(data.job.kind)) {
        state.job = data.job;
        if (data.job.kind === "train") renderTrainProgress(data.job);
        else if (data.job.kind === "build") renderBuildProgress(data.job);
        else renderProgress(data.job);
        startJobPolling();
      }
    } catch {
      /* 没有活动任务时忽略 */
    }
  }

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("hashchange", () => {
    if (location.hash === "#voices" && state.initialized) {
      loadEngine();
      loadLocalizedBanks();
    }
  });
  window.addEventListener("wt:line-tuning-saved", (event) => {
    const detail = event.detail || {};
    if (!detail.project || detail.project.id !== state.current?.id) return;
    if (detail.needsSynthesis) stopVoicePlayback(true, { reset: true });
    selectProject(detail.project.id).catch((error) =>
      toastMessage(error.message, "bad"),
    );
  });
})();
