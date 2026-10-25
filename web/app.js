const $ = (selector) => document.querySelector(selector);

const ui = {
  data: null,
  components: null,
  job: null,
  pollTimer: null,
  toastTimer: null,
  storage: null,
  storageTimer: null,
  pendingUninstall: null,
  pendingInstall: null,
};

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);

const formatMb = (bytes) => `${(Number(bytes || 0) / 1024 / 1024).toFixed(1)} MB`;
const formatGb = (bytes) => `${(Number(bytes || 0) / 1024 ** 3).toFixed(1)} GB`;
const formatStorageSize = (bytes) => {
  const value = Number(bytes || 0);
  if (!value) return "0 B";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
};
const formatShare = (value) => {
  const share = Number(value || 0);
  if (!share) return "0%";
  if (share < 0.1) return "<0.1%";
  return `${share.toFixed(share < 10 ? 1 : 0)}%`;
};
const formatSpeed = (bytes) => (bytes ? `${formatMb(bytes)}/s` : "—");
const formatEta = (seconds) => {
  const total = Math.max(0, Math.round(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours) return `${hours} 小时 ${minutes} 分`;
  if (minutes) return `${minutes} 分 ${rest} 秒`;
  return `${rest} 秒`;
};
const formatDate = (value) => (value ? String(value).replace("T", " ").slice(0, 19) : "—");

async function api(path, options = {}) {
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
  if (!response.ok) {
    const error = new Error(payload.error || `请求失败（${response.status}）`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

function setGlobalStatus(text, tone = "ok") {
  const element = $("#global-status");
  element.className = `status-pill ${tone === "ok" ? "" : tone}`.trim();
  element.innerHTML = `<span class="dot"></span> ${escapeHtml(text)}`;
}

function setPill(selector, text, tone = "") {
  const element = $(selector);
  element.textContent = text;
  element.className = `pill ${tone}`.trim();
}

function showError(message) {
  const banner = $("#error-banner");
  banner.textContent = message;
  banner.hidden = false;
}

function clearError() {
  const banner = $("#error-banner");
  banner.textContent = "";
  banner.hidden = true;
}

function toast(message, tone = "ok") {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast ${tone === "bad" ? "bad" : ""}`.trim();
  element.hidden = false;
  clearTimeout(ui.toastTimer);
  ui.toastTimer = setTimeout(() => {
    element.hidden = true;
  }, 3600);
}

function kvRows(rows) {
  return `<div class="kv">${rows
    .map(
      ([key, value]) =>
        `<div class="kv-row"><span>${escapeHtml(key)}</span><span>${escapeHtml(value)}</span></div>`,
    )
    .join("")}</div>`;
}

async function loadState() {
  setGlobalStatus("正在读取环境", "busy");
  try {
    const data = await api("/api/state");
    ui.data = data;
    render(data);
    setGlobalStatus(`环境就绪 · ${data.app.milestone}`, "ok");
    clearError();
  } catch (error) {
    showError(error.message);
    setGlobalStatus("环境读取失败", "bad");
  }
}

function render(data) {
  $("#version-label").textContent = `v${data.app.version} · ${data.app.milestone}`;
  renderGame(data);
  renderKit(data);
  renderGpu(data);
  renderEngine(data);
  renderData(data);
  renderSetupState(data);
  renderSetupOverview(data);
  renderBanks(data);
  window.dispatchEvent(new CustomEvent("wt:state", { detail: data }));
}

function renderSetupState(data) {
  const panel = $("#setup-panel");
  const pill = $("#setup-pill");
  if (!panel || !pill) return;
  const missing = [];
  if (!data.selected_game) missing.push("战雷目录");
  if (!(data.kit && data.kit.valid)) missing.push("素材工程");
  pill.textContent = missing.length ? `目录还差 ${missing.length} 项` : "目录已就绪";
  pill.className = `pill ${missing.length ? "warn" : "ok"}`;
  if (!panel.dataset.initialized) {
    panel.open = missing.length > 0;
    panel.dataset.initialized = "1";
  }
}

function setupChecklist(data) {
  const fmod = (data.fmod_studio || []).length > 0;
  const vgm = Boolean(data.vgmstream && data.vgmstream.available);
  return [
    {
      key: "gpt",
      label: "GPT-SoVITS",
      ok: Boolean(data.gpt_sovits && data.gpt_sovits.available),
      optional: false,
      jump: "setup-row-gpt",
    },
    {
      key: "game",
      label: "战雷目录",
      ok: Boolean(data.selected_game),
      optional: false,
      jump: "setup-row-game",
    },
    {
      key: "kit",
      label: "官方工程",
      ok: Boolean(data.kit && data.kit.valid),
      optional: false,
      jump: "setup-row-kit",
    },
    {
      key: "fmod",
      label: "FMOD CLI",
      ok: fmod,
      optional: false,
      jump: "setup-row-fmod",
    },
    {
      key: "vgm",
      label: "vgmstream",
      ok: vgm,
      optional: false,
      jump: "setup-row-vgm",
    },
    {
      key: "rvc",
      label: "RVC 换声",
      ok: Boolean(data.rvc && data.rvc.available),
      optional: true,
      jump: "setup-row-rvc",
    },
  ];
}

function renderSetupOverview(data = ui.data) {
  if (!data) return;
  const items = setupChecklist(data);
  const missing = items.filter((item) => !item.ok && !item.optional);
  const optionalMissing = items.filter((item) => !item.ok && item.optional);
  const checks = $("#setup-checks");
  if (checks) {
    checks.innerHTML = items
      .map((item) => {
        const tone = item.ok ? "ok" : item.optional ? "optional" : "warn";
        const mark = item.ok ? "✓" : item.optional ? "○" : "!";
        return (
          `<button class="setup-check ${tone}" type="button" data-setup-jump="${item.jump}">` +
          `<span>${mark}</span>${escapeHtml(item.label)}</button>`
        );
      })
      .join("");
  }
  const readyCount = items.filter((item) => item.ok).length;
  const requiredCount = items.filter((item) => !item.optional).length;
  const headline = $("#setup-headline");
  const detail = $("#setup-detail");
  const status = $("#setup-status");
  if (status) {
    status.className = `status-pill ${missing.length ? "busy" : ""}`.trim();
    status.innerHTML = `<span class="dot"></span> ${missing.length ? `还差 ${missing.length} 项` : "环境就绪"}`;
  }
  const startLink = $("#setup-skip");
  if (startLink) {
    startLink.textContent = missing.length
      ? "先制作，稍后补环境"
      : optionalMissing.length
        ? "开始制作（RVC 稍后再装）"
        : "开始制作";
    startLink.className = `btn ${missing.length ? "ghost" : ""}`.trim();
  }
  if (headline && detail) {
    if (missing.length) {
      headline.textContent = `还差 ${missing.length} 项就能开始制作`;
      detail.textContent = `缺少：${missing.map((item) => item.label).join("、")}。下面每项都给出下载或选择入口。`;
    } else if (optionalMissing.length) {
      headline.textContent = "核心环境已就绪";
      detail.textContent = "GPT-SoVITS、游戏目录、官方工程和构建工具都齐了；RVC 换声是可选项，需要时再装。";
    } else {
      headline.textContent = "全部就绪，可以开始制作";
      detail.textContent = "需要的东西都在本机；下一步打开工作台，导入一段参考音频按四步制作。";
    }
  }
  const pill = $("#setup-component-pill");
  if (pill) {
    pill.textContent = `必需 ${requiredCount - missing.length}/${requiredCount} 已就绪`;
    pill.className = `pill ${missing.length ? "warn" : "ok"}`;
  }
  renderSetupRows(data);
  renderRvcSetup();
  renderUninstallButtons();
  syncSetupHint(undefined, data);
}

function renderSetupRows(data) {
  const gpt = data.gpt_sovits || {};
  setSetupPill("#setup-gpt-pill", gpt.available ? "已就绪" : "未安装", gpt.available ? "ok" : "warn");
  const gptNote = $("#setup-gpt-note");
  if (gptNote) {
    gptNote.textContent = gpt.available
      ? `已启用：${gpt.path || "已找到运行时"}。需要重装时可以继续用下面的安装面板。`
      : "中文配音和音色克隆必需；压缩包约 7.5 GB，下载后自动校验和解压。";
  }
  const gptButton = $("#setup-gpt-btn");
  if (gptButton) gptButton.textContent = gpt.available ? "查看状态" : "去安装";

  const rvc = data.rvc || {};
  const rvcNote = $("#setup-rvc-note");
  if (rvcNote) {
    if (rvc.available) {
      rvcNote.textContent = `已启用：${rvc.path || "已找到 RVC 运行时"}。`;
    } else {
      const info = ui.components;
      const id = info && info.rvc_component;
      const item = id ? (info.catalog || []).find((entry) => entry.id === id) : null;
      rvcNote.textContent = item
        ? `只在“原语言只换音色”时需要；会按显卡选 ${item.name}（约 ${formatGb(item.size)}）。`
        : "只在“原语言只换音色”时需要；安装包约 5-8 GB。";
    }
  }

  const fmodList = data.fmod_studio || [];
  setSetupPill("#setup-fmod-pill", fmodList.length ? "已安装" : "未安装", fmodList.length ? "ok" : "warn");
  const fmodNote = $("#setup-fmod-note");
  if (fmodNote) {
    const first = fmodList[0];
    fmodNote.textContent = first
      ? `${first.version || "FMOD Studio"} · ${first.path}`
      : "构建 bank 必需；需要 Firelight 账号，因此从官方页面下载安装。";
  }

  const kit = data.kit || {};
  setSetupPill("#setup-kit-pill", kit.valid ? "已就绪" : "未选择", kit.valid ? "ok" : "warn");
  const kitNote = $("#setup-kit-note");
  if (kitNote) {
    kitNote.textContent = kit.valid
      ? `已读取：${kit.path}`
      : "事件名、素材路径和分类都来自这里；从官方 GitHub 仓库下载后选择一次。";
  }
  const kitButton = $("#setup-kit-btn");
  if (kitButton) kitButton.textContent = kit.valid ? "更换" : "去选择";

  const vgm = data.vgmstream || {};
  setSetupPill("#setup-vgm-pill", vgm.available ? "已就绪" : "缺失", vgm.available ? "ok" : "warn");
  const vgmBadge = $("#setup-vgm-badge");
  if (vgmBadge) vgmBadge.textContent = vgm.bundled ? "内置" : vgm.available ? "已找到" : "缺失";
  const vgmNote = $("#setup-vgm-note");
  if (vgmNote) {
    vgmNote.textContent = vgm.available
      ? `已就绪${vgm.bundled ? "（随程序内置）" : `：${vgm.path}`}；不需要单独安装。`
      : "用于从游戏 bank 取出原音；打包版通常已内置，缺失时会自动下载。";
  }

  const game = data.selected_game;
  setSetupPill("#setup-game-pill", game ? "已选择" : "未选择", game ? "ok" : "warn");
  const gameNote = $("#setup-game-note");
  if (gameNote) {
    gameNote.textContent = game
      ? `${game.path} · ${game.bank_count} 个 bank`
      : "用来读取原音，并把成品写入 sound/mod；游戏目录本身只读。";
  }
  const gameButton = $("#setup-game-btn");
  if (gameButton) gameButton.textContent = game ? "更换" : "去选择";

  const disk = $("#setup-disk");
  if (disk) {
    const info = ui.components;
    const free = Number(info && info.free_bytes);
    const need = Number(info && info.required_bytes && info.required_bytes["gpt-sovits"]);
    if (free) {
      disk.className = `setup-disk${need && free < need ? " bad" : ""}`;
      disk.innerHTML = `安装盘可用 <strong>${formatGb(free)}</strong>` +
        (need ? `<br>GPT-SoVITS 下载 + 解压约需 ${formatGb(need)}` : "");
    } else {
      disk.textContent = "磁盘空间读取中…";
    }
  }
}

const STORAGE_TAGS = {
  required: ["必需", "subtle"],
  optional: ["可选", "subtle"],
  data: ["数据", "subtle"],
  cleanable: ["可清理", "warn"],
};
const CLEAN_ITEMS = ["cache", "logs", "archives", "webview"];

function cleanableStorageItems(data = ui.storage) {
  const items = Array.isArray(data?.items) ? data.items : [];
  const cleanable = Array.isArray(data?.cleanable?.items) ? data.cleanable.items : null;
  if (cleanable) {
    return cleanable
      .map((entry) => {
        const meta = items.find((item) => item.id === entry.id) || {};
        return { ...meta, id: entry.id, bytes: Number(entry.bytes || 0) };
      })
      .filter((item) => Number(item.bytes) > 0);
  }
  return CLEAN_ITEMS.map((id) => items.find((item) => item.id === id)).filter(
    (item) => item && Number(item.bytes) > 0,
  );
}

function renderStorage(data) {
  const totalBytes = Number(data?.total_bytes || 0);
  const scanning = Boolean(data?.scanning);
  const pill = $("#storage-total");
  if (pill) {
    pill.textContent =
      data?.ok === false
        ? "统计失败"
        : scanning
          ? totalBytes
            ? `${formatStorageSize(totalBytes)} · 更新中`
            : "统计中…"
          : formatStorageSize(totalBytes);
    pill.className = `pill ${data?.ok === false ? "bad" : "subtle"}`;
  }
  const path = $("#storage-path");
  if (path) {
    path.textContent = data?.data_dir || "data";
    path.title = data?.data_dir || "";
  }
  const summary = $("#storage-summary-text");
  if (summary) {
    if (data?.ok === false) {
      summary.textContent = data.error || "统计失败";
    } else if (scanning && !data?.cached) {
      summary.textContent = "正在后台统计，界面可以先继续用。";
    } else {
      const files = Number(data?.total_files || 0).toLocaleString("zh-CN");
      const time = data?.scanned_at
        ? new Date(data.scanned_at * 1000).toLocaleTimeString("zh-CN", { hour12: false })
        : "";
      summary.textContent = `共 ${files} 个文件${time ? ` · ${time} 更新` : ""}${scanning ? " · 正在更新" : ""}`;
    }
  }
  const free = $("#storage-free");
  if (free) {
    const freeBytes = Number(data?.disk_free_bytes || 0);
    const diskBytes = Number(data?.disk_total_bytes || 0);
    free.textContent = diskBytes
      ? `磁盘剩余 ${formatStorageSize(freeBytes)} / 共 ${formatStorageSize(diskBytes)}`
      : "磁盘剩余 —";
  }
  const items = Array.isArray(data?.items) ? data.items : [];
  const visible = items.filter((item) => Number(item.bytes) > 0);
  const cleanBytes = cleanableStorageItems(data).reduce(
    (sum, item) => sum + Number(item.bytes || 0),
    0,
  );
  const cleanButton = $("#storage-clean-btn");
  if (cleanButton) {
    cleanButton.hidden = cleanBytes <= 0;
    cleanButton.textContent = cleanBytes
      ? `清理可回收 ${formatStorageSize(cleanBytes)}`
      : "清理可回收空间";
  }
  const bar = $("#storage-bar");
  if (bar) {
    bar.innerHTML = visible.length
      ? visible
          .map(
            (item) =>
              `<span class="storage-seg-${escapeHtml(item.id)}" style="width:${Math.max(0.6, Number(item.share) || 0)}%" title="${escapeHtml(item.name)} ${formatStorageSize(item.bytes)}"></span>`,
          )
          .join("")
      : '<span class="storage-placeholder"></span>';
  }
  const list = $("#storage-list");
  if (!list) return;
  if (!visible.length) {
    list.innerHTML = `<p class="hint small">${
      data?.ok === false
        ? escapeHtml(data.error || "统计失败")
        : scanning
          ? "正在后台统计…"
          : "数据目录目前为空。"
    }</p>`;
    return;
  }
  const cleanableMap = new Map(
    (Array.isArray(data?.cleanable?.items) ? data.cleanable.items : []).map((entry) => [
      entry.id,
      Number(entry.bytes || 0),
    ]),
  );
  list.innerHTML = visible
    .map((item) => {
      let tag = STORAGE_TAGS[item.kind];
      if (item.id === "webview" && !cleanableMap.get("webview")) {
        tag = ["保留配置", "subtle"];
      }
      const tagHtml = tag ? `<span class="pill ${tag[1]} storage-row-tag">${tag[0]}</span>` : "";
      const share = Number(item.share) || 0;
      return `<article class="storage-row" title="${escapeHtml(`${Number(item.files || 0).toLocaleString("zh-CN")} 个文件`)}">
        <span class="storage-row-icon"><svg class="icon" aria-hidden="true"><use href="#${escapeHtml(item.icon || "i-folder")}"></use></svg></span>
        <div class="storage-row-main">
          <div class="storage-row-head"><strong>${escapeHtml(item.name)}</strong><span class="storage-row-size">${formatStorageSize(item.bytes)}</span></div>
          <p>${escapeHtml(item.description)} <span class="muted">· ${formatShare(share)}</span></p>
          <div class="storage-row-bar"><span class="storage-seg-${escapeHtml(item.id)}" style="width:${Math.max(1, share)}%"></span></div>
        </div>
        ${tagHtml}
      </article>`;
    })
    .join("");
}

async function loadStorage(force = false) {
  clearTimeout(ui.storageTimer);
  try {
    const data = force
      ? await api("/api/storage/refresh", { method: "POST", body: "{}" })
      : await api("/api/storage");
    ui.storage = data;
    renderStorage(data);
    if (data.scanning) ui.storageTimer = setTimeout(() => loadStorage(false), 1200);
  } catch (error) {
    const pill = $("#storage-total");
    if (pill) {
      pill.textContent = "统计失败";
      pill.className = "pill bad";
    }
    const summary = $("#storage-summary-text");
    if (summary) summary.textContent = error.message;
  }
}

function openCleanupDialog() {
  if (!ui.storage) {
    toast("正在统计磁盘占用，请稍等", "bad");
    loadStorage(true);
    return;
  }
  const rows = cleanableStorageItems();
  if (!rows.length) {
    toast("当前没有可清理的内容");
    return;
  }
  const list = $("#storage-clean-list");
  if (list) {
    list.innerHTML = rows
      .map(
        (item) =>
          `<div class="clean-row"><span>${escapeHtml(item.name)}</span><span>${formatStorageSize(item.bytes)}</span></div>`,
      )
      .join("");
  }
  const total = rows.reduce((sum, item) => sum + Number(item.bytes || 0), 0);
  const note = $("#storage-clean-note");
  if (note) {
    note.textContent = ui.job?.active
      ? `可回收约 ${formatStorageSize(total)}；组件任务正在运行，安装包残留会跳过。`
      : `可回收约 ${formatStorageSize(total)}；正在使用的缓存文件会自动跳过。`;
  }
  const start = $("#storage-clean-start");
  if (start) {
    start.disabled = false;
    start.textContent = "清理这些文件";
  }
  const dialog = $("#storage-clean-dialog");
  if (dialog && !dialog.open) dialog.showModal();
}

async function startCleanup() {
  const rows = cleanableStorageItems();
  if (!rows.length) return;
  const button = $("#storage-clean-start");
  if (button) {
    button.disabled = true;
    button.textContent = "正在清理…";
  }
  try {
    const payload = await api("/api/storage/clean", {
      method: "POST",
      body: JSON.stringify({ categories: rows.map((item) => item.id) }),
    });
    $("#storage-clean-dialog")?.close();
    if (payload.storage) {
      ui.storage = payload.storage;
      renderStorage(payload.storage);
      if (payload.storage.scanning) {
        clearTimeout(ui.storageTimer);
        ui.storageTimer = setTimeout(() => loadStorage(false), 1200);
      }
    }
    const freed = formatStorageSize(payload.freed_bytes || 0);
    const skipped = (payload.skipped || [])
      .filter(Boolean)
      .map((item) => `；${item}`)
      .join("");
    toast(`已清理 ${freed}（${payload.removed_files || 0} 个文件）${skipped}`);
  } catch (error) {
    toast(error.message, "bad");
    if (button) {
      button.disabled = false;
      button.textContent = "清理这些文件";
    }
  }
}

function renderUninstallButtons() {
  const info = ui.components;
  const gpt = info?.uninstall?.gpt;
  const rvc = info?.uninstall?.rvc;
  const gptButton = $("#setup-gpt-uninstall");
  if (gptButton) gptButton.hidden = !(gpt && gpt.available);
  const rvcButton = $("#setup-rvc-uninstall");
  if (rvcButton) rvcButton.hidden = !(rvc && rvc.available);
}

function openComponentUninstall(kind) {
  const target = ui.components?.uninstall?.[kind];
  if (!target || !target.available) {
    toast(target?.reason || "没有可卸载的运行时", "bad");
    return;
  }
  ui.pendingUninstall = { kind: kind === "gpt" ? "gpt-sovits" : "rvc", target };
  const text = $("#component-uninstall-text");
  if (text) {
    text.innerHTML =
      `会删除 <strong>${escapeHtml(target.label || kind.toUpperCase())}</strong> 运行时：` +
      `<span class="mono">${escapeHtml(target.folder || target.path)}</span>。` +
      "卸载后需要重新下载才能合成或换声。";
  }
  const button = $("#component-uninstall-start");
  if (button) {
    button.disabled = false;
    button.textContent = "卸载并删除运行时";
  }
  const dialog = $("#component-uninstall-dialog");
  if (dialog && !dialog.open) dialog.showModal();
}

async function startComponentUninstall() {
  const pending = ui.pendingUninstall;
  if (!pending) return;
  const button = $("#component-uninstall-start");
  if (button) {
    button.disabled = true;
    button.textContent = "正在卸载…";
  }
  try {
    const payload = await api("/api/components/uninstall", {
      method: "POST",
      body: JSON.stringify({ id: pending.kind }),
    });
    $("#component-uninstall-dialog")?.close();
    ui.pendingUninstall = null;
    if (payload.components) {
      ui.components = payload.components;
      ui.job = payload.components.job || null;
      renderComponentsPanel();
      renderRvcSetup();
      renderUninstallButtons();
    }
    if (payload.storage) {
      ui.storage = payload.storage;
      renderStorage(payload.storage);
    }
    toast(
      `已卸载 ${payload.name || "运行时"}，释放 ${formatStorageSize(payload.freed_bytes || 0)}`,
    );
    await loadState();
    await loadComponents();
    loadStorage(true);
  } catch (error) {
    toast(error.message, "bad");
    if (button) {
      button.disabled = false;
      button.textContent = "卸载并删除运行时";
    }
  }
}

function setSetupPill(selector, text, tone = "subtle") {
  const node = $(selector);
  if (!node) return;
  node.textContent = text;
  node.className = `pill ${tone}`;
}

function currentRvcJob() {
  const job = ui.job || (ui.components && ui.components.job);
  return job && String(job.component || "").startsWith("rvc") ? job : null;
}

function renderRvcSetup() {
  const pill = $("#setup-rvc-pill");
  if (!pill) return;
  const rvc = (ui.data && ui.data.rvc) || (ui.components && ui.components.rvc) || {};
  const job = currentRvcJob();
  const active = Boolean(job && job.active);
  const installed = Boolean(rvc.available) && !active;
  const button = $("#setup-rvc-btn");
  const cancel = $("#setup-rvc-cancel");
  const progress = $("#setup-rvc-progress");
  if (installed) {
    setSetupPill("#setup-rvc-pill", "已就绪", "ok");
    if (button) button.hidden = true;
    if (cancel) cancel.hidden = true;
    if (progress) progress.hidden = true;
    return;
  }
  if (active) {
    setSetupPill("#setup-rvc-pill", "安装中", "warn");
    if (button) button.hidden = true;
    if (cancel) cancel.hidden = false;
    if (progress) progress.hidden = false;
    return;
  }
  setSetupPill("#setup-rvc-pill", job && job.state === "error" ? "安装失败" : "未安装", job && job.state === "error" ? "bad" : "warn");
  if (button) {
    button.hidden = false;
    button.disabled = false;
    button.textContent = job && (job.state === "error" || job.state === "cancelled") ? "重试（续传）" : "下载并安装";
  }
  if (cancel) cancel.hidden = true;
  if (progress) {
    const show = job && (job.state === "error" || job.state === "cancelled" || job.state === "done");
    progress.hidden = !show;
    if (show) {
      $("#setup-rvc-fill").style.width = `${Math.max(0, Math.min(100, Number(job.percent || 0)))}%`;
      $("#setup-rvc-text").textContent =
        job.state === "error" ? `失败：${job.error || job.message}` : job.message || "已停止";
    }
  }
}

function renderGame(data) {
  const selected = data.selected_game;
  const installs = data.installs || [];
  const gamePathInput = $("#game-path");

  if (selected) {
    setPill("#game-pill", "已选择", "ok");
    if (!gamePathInput.value) gamePathInput.value = selected.path;
    $("#game-status").innerHTML = kvRows([
      ["路径", selected.path],
      ["aces.exe", selected.aces_exe || "—"],
      ["bank 数量", String(selected.bank_count)],
      ["sound\\mod 文件", String(selected.mod_file_count)],
      ["enable_mod", selected.enable_mod === null ? "未找到" : selected.enable_mod ? "yes" : "no"],
      ["fmod_sound_enable", selected.fmod_sound_enable === null ? "未找到" : selected.fmod_sound_enable ? "yes" : "no"],
      ["aces.exe 修改时间", formatDate(selected.aces_modified)],
    ]);
  } else {
    setPill("#game-pill", installs.length ? "待选择" : "未找到", installs.length ? "warn" : "");
    $("#game-status").innerHTML = installs.length
      ? `<p class="muted">找到 ${installs.length} 个战雷目录，选择一个开始。</p>`
      : `<p class="muted">没有自动找到战雷。可以手动填入目录，或设置环境变量 WT_PATH。</p>`;
  }

  const installBox = $("#game-installs");
  if (!installs.length) {
    installBox.innerHTML = "";
    return;
  }
  installBox.innerHTML = installs
    .map(
      (item) => `
      <div class="install-row">
        <div>
          <div class="mono">${escapeHtml(item.path)}</div>
          <div class="muted small">${item.bank_count} 个 bank · ${item.mod_file_count} 个 mod 文件</div>
        </div>
        <button class="btn small" type="button" data-select-game="${escapeHtml(item.path)}">使用</button>
      </div>`,
    )
    .join("");
}

function renderKit(data) {
  const kit = data.kit;
  const input = $("#kit-path");

  if (kit && kit.valid) {
    setPill("#kit-pill", "已就绪", "ok");
    if (!input.value) input.value = kit.path;
    $("#kit-status").innerHTML = kvRows([
      ["路径", kit.path],
      ["工程文件", kit.fspro ? kit.fspro.split(/[\\/]/).pop() : "—"],
      ["Bank 元数据", `${kit.bank_xml_count} 个`],
      ["事件元数据", `${kit.event_xml_count} 个`],
      ["素材目录", `${kit.asset_dir_count} 个`],
      ["修改时间", formatDate(kit.modified)],
    ]);
  } else {
    setPill("#kit-pill", "未选择");
    $("#kit-status").innerHTML = `<p class="muted">选择一个 Gaijin 官方 FMOD 工程目录。只读，不会修改原工程。</p>`;
  }

  const candidates = data.kit_candidates || [];
  const box = $("#kit-candidates");
  if (!candidates.length) {
    box.innerHTML = `<span class="muted small">没有自动找到，填一次路径就行。</span>`;
    return;
  }
  box.innerHTML = candidates
    .map(
      (item) => `
      <div class="install-row">
        <div>
          <div class="mono">${escapeHtml(item.path)}</div>
          <div class="muted small">${item.bank_xml_count} banks · ${item.event_xml_count} events</div>
        </div>
        <button class="btn small" type="button" data-select-kit="${escapeHtml(item.path)}">使用</button>
      </div>`,
    )
    .join("");
}

function renderGpu(data) {
  const gpu = data.gpu || {};
  const gpus = gpu.gpus || [];
  setPill("#gpu-pill", gpu.recommended_label || "CPU", gpus.length ? "ok" : "warn");

  const gpuRows = gpus.map((item) => {
    const vram = item.vram_mb ? ` · ${(item.vram_mb / 1024).toFixed(1)} GB` : "";
    return `${item.name}${vram} · ${item.backend_label}`;
  });

  const notes = (gpu.notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join("");
  $("#gpu-status").innerHTML = `
    ${kvRows([
      ["推荐后端", gpu.recommended_label || "CPU"],
      ["显卡", gpuRows.length ? gpuRows.join(" / ") : "未检测到独显"],
      ["CPU 兜底", gpu.cpu_only ? "是" : "可用"],
    ])}
    ${notes ? `<ul class="notes">${notes}</ul>` : ""}
  `;
}

function renderEngine(data) {
  const engine = data.gpt_sovits || {};
  const input = $("#engine-path");
  if (engine.available) {
    if (!input.value) input.value = engine.path;
    $("#engine-status").innerHTML =
      `<div class="status-line"><span class="pill ok">GPT-SoVITS 已就绪</span></div>` +
      kvRows([
        ["路径", engine.path],
        ["状态", engine.running ? `API 运行中 · 端口 ${engine.port}` : "未启动，合成时自动拉起"],
        ["Python", engine.python || "—"],
      ]);
  } else {
    $("#engine-status").innerHTML =
      `<div class="status-line"><span class="pill warn">未安装</span></div>` +
      `<p class="hint">${escapeHtml(engine.detail || "未检测到 GPT-SoVITS。")}</p>`;
  }
  renderComponentsPanel();
}

function renderData(data) {
  const status = $("#data-status");
  if (!status) return;
  const cfg = data.config || {};
  const dataDir = cfg.data_dir || "—";
  status.innerHTML = `<div class="path-box mono">${escapeHtml(dataDir)}</div>`;
}

function configPath(target) {
  const cfg = (ui.data && ui.data.config) || {};
  return {
    data: cfg.data_dir || "",
    game: cfg.game_path || "",
    kit: cfg.kit_path || "",
    rvc: cfg.gpt_sovits_path || "",
  }[target] || "";
}

async function openFolder(target) {
  try {
    const result = await api("/api/system/open-folder", {
      method: "POST",
      body: JSON.stringify({ target }),
    });
    toast(`已打开：${result.path}`);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function copyPath(target) {
  const path = configPath(target);
  if (!path) {
    toast("这个目录还没有设置", "bad");
    return;
  }
  try {
    await navigator.clipboard.writeText(path);
    toast("路径已复制");
  } catch {
    toast(`复制失败，请手动复制：${path}`, "bad");
  }
}

function syncUninstallChoice() {
  const keep = Boolean($("#uninstall-keep-data")?.checked);
  const warning = $("#uninstall-warning");
  const button = $("#uninstall-start");
  if (warning) {
    warning.classList.toggle("keep", keep);
    warning.textContent = keep
      ? "将保留 data 文件夹；重新安装后可以继续使用。"
      : "不勾选会同时删除 data 文件夹，删除后无法恢复。";
  }
  if (button) button.textContent = keep ? "卸载并保留 data" : "卸载并删除全部数据";
}

function openUninstallDialog() {
  const dialog = $("#uninstall-dialog");
  if (!dialog) return;
  const checkbox = $("#uninstall-keep-data");
  if (checkbox) checkbox.checked = false;
  const button = $("#uninstall-start");
  if (button) button.disabled = false;
  syncUninstallChoice();
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

async function startUninstall() {
  const keep = Boolean($("#uninstall-keep-data")?.checked);
  const button = $("#uninstall-start");
  if (button) {
    button.disabled = true;
    button.textContent = "正在打开卸载程序…";
  }
  try {
    await api("/api/system/uninstall", {
      method: "POST",
      body: JSON.stringify({ keep_data: keep }),
    });
    toast("已启动卸载程序，软件将在几秒内关闭");
    $("#uninstall-dialog")?.close();
  } catch (error) {
    toast(error.message, "bad");
    if (button) {
      button.disabled = false;
      syncUninstallChoice();
    }
  }
}

const SETUP_HINT_KEY = "wtvf.setup-hint-seen";

function currentRoute() {
  return (location.hash || "#setup").replace(/^#/, "");
}

function setupHintSeen() {
  try {
    return localStorage.getItem(SETUP_HINT_KEY) === "1";
  } catch {
    return false;
  }
}

function dismissSetupHint() {
  const tag = $("#nav-setup-hint");
  if (tag) tag.hidden = true;
  try {
    localStorage.setItem(SETUP_HINT_KEY, "1");
  } catch {
    /* localStorage 不可用时只隐藏本次会话 */
  }
}

function syncSetupHint(route = currentRoute(), data = ui.data) {
  const tag = $("#nav-setup-hint");
  if (!tag) return;
  if (setupHintSeen()) {
    tag.hidden = true;
    return;
  }
  if (route !== "setup") {
    dismissSetupHint();
    return;
  }
  if (!data) return;
  const missing = setupChecklist(data).filter((item) => !item.ok && !item.optional);
  if (!missing.length) dismissSetupHint();
}

function routePages() {
  const requested = (location.hash || "#setup").replace(/^#/, "");
  const target = requested === "quick" ? "workbench" : requested;
  const key = ["setup", "workbench", "voices", "packs", "assets"].includes(target)
    ? target
    : "setup";
  if (requested === "quick") history.replaceState(null, "", "#workbench");
  const titles = {
    setup: "环境准备 · WT Voice Forge",
    workbench: "工作台 · WT Voice Forge",
    voices: "高级编辑 · WT Voice Forge",
    packs: "语音包 · WT Voice Forge",
    assets: "游戏素材 · WT Voice Forge",
  };
  document.querySelectorAll(".page-view").forEach((page) => {
    page.hidden = page.id !== `page-${key}`;
  });
  document.querySelectorAll(".nav-item[data-route]").forEach((item) => {
    item.classList.toggle("active", item.dataset.route === key);
  });
  syncSetupHint(key);
  document.title = titles[key];
  window.scrollTo(0, 0);
}

async function loadComponents() {
  try {
    const info = await api("/api/components");
    ui.components = info;
    if (info.job) ui.job = info.job;
    renderComponentsPanel();
    renderSetupOverview();
    if (ui.job && ui.job.active) startPolling();
  } catch (error) {
    ui.components = null;
  }
}

function populatePackages(info) {
  const select = $("#engine-package");
  if (select.dataset.built === "1") return;
  const catalog = (info.catalog || []).filter((item) => item.kind === "gpt_sovits");
  select.innerHTML = catalog
    .map(
      (item) =>
        `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${formatGb(item.size)}</option>`,
    )
    .join("");
  if (catalog.length) select.value = catalog[0].id;
  select.dataset.built = "1";
  updatePackageMeta();
}

function currentPackage() {
  if (!ui.components) return null;
  const id = $("#engine-package").value;
  return (ui.components.catalog || []).find((item) => item.id === id) || null;
}

function updatePackageMeta() {
  const item = currentPackage();
  const info = ui.components;
  if (!item || !info) return;
  const need = (info.required_bytes || {})[item.id] || item.size * 2;
  const free = Number(info.free_bytes || 0);
  const short = free > 0 && free < need;
  const slim = Boolean($("#engine-slim")?.checked);
  $("#engine-package-note").textContent = `${item.recommend} · ${item.note}`;
  const meta = $("#engine-install-meta");
  meta.className = `install-meta${short ? " bad" : ""}`.trim();
  meta.innerHTML =
    `下载 <strong>${formatGb(item.size)}</strong> · 解压需预留 ${formatGb(need)} · ` +
    `当前可用 ${free ? formatGb(free) : "未知"}` +
    (slim ? "<br>精简安装装好后约少 1.9 GB；解压时仍需要完整空间。" : "") +
    (short ? "<br>磁盘空间不足，请换一个安装位置。" : "");
}

async function requestPreflight(pending) {
  try {
    const data = await api("/api/components/preflight", {
      method: "POST",
      body: JSON.stringify({ id: pending.id, target: pending.target, slim: pending.slim }),
    });
    ui.pendingInstall = { ...pending, report: data };
    const title = $("#install-preflight-title");
    if (title) title.textContent = `安装 ${data.name}`;
    const intro = $("#install-preflight-intro");
    if (intro) {
      intro.textContent = "先核对下载体积和目标盘空间，确认后开始。取消不会下载任何文件。";
    }
    const rows = [
      ["下载包", formatGb(data.download_bytes)],
      ["安装时需预留", formatGb(data.required_bytes)],
      ["目标盘可用", data.free_bytes ? formatGb(data.free_bytes) : "未知"],
      ["安装位置", pending.target || data.target || "默认位置"],
    ];
    if (data.slim) {
      rows.push(["精简安装", `约少 ${formatGb(data.slim_estimate_bytes)}（去掉自动听写和人声分离）`]);
    }
    const list = $("#install-preflight-list");
    if (list) {
      list.innerHTML = rows
        .map(
          ([label, value]) =>
            `<div class="clean-row"><span>${escapeHtml(label)}</span><span class="mono">${escapeHtml(value)}</span></div>`,
        )
        .join("");
    }
    const warning = $("#install-preflight-warning");
    if (warning) {
      const messages = [...(data.warnings || [])];
      warning.hidden = messages.length === 0;
      warning.textContent = messages.join(" ");
    }
    const start = $("#install-preflight-start");
    if (start) {
      start.disabled = !data.ok;
      start.textContent = data.ok ? "开始下载" : "磁盘空间不足";
    }
    const dialog = $("#install-preflight-dialog");
    if (dialog && !dialog.open) dialog.showModal();
  } catch (error) {
    ui.pendingInstall = null;
    showError(error.message);
    toast(error.message, "bad");
  }
}

async function startPreflightDownload() {
  const pending = ui.pendingInstall;
  if (!pending || !pending.report?.ok) return;
  const button = $("#install-preflight-start");
  if (button) {
    button.disabled = true;
    button.textContent = "正在启动…";
  }
  clearError();
  try {
    const payload = await api("/api/components/download", {
      method: "POST",
      body: JSON.stringify({
        id: pending.id,
        target: pending.target,
        source: pending.source,
        keep_archive: pending.keep_archive,
        slim: pending.slim,
      }),
    });
    $("#install-preflight-dialog")?.close();
    ui.pendingInstall = null;
    toast(pending.slim ? "已开始下载（精简安装），可随时取消" : "已开始下载，可随时取消");
    applyJob(payload.job);
    startPolling();
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "开始下载";
    }
    showError(error.message);
    toast(error.message, "bad");
    if (error.payload && error.payload.job) applyJob(error.payload.job);
  }
}

function renderJob(job) {
  const progress = $("#engine-progress");
  const button = $("#engine-download-btn");
  const cancel = $("#engine-cancel-btn");
  if (!job) {
    progress.hidden = true;
    button.disabled = false;
    button.textContent = "下载并安装";
    cancel.hidden = true;
    return;
  }

  progress.hidden = false;
  let percent = Number(job.percent || 0);
  let text = job.message || "";
  if (job.state === "downloading") {
    percent = Number(job.percent || 0);
    text = `下载 ${percent.toFixed(1)}% · ${formatSpeed(job.speed)}` +
      (job.eta ? ` · 剩余 ${formatEta(job.eta)}` : "");
  } else if (job.state === "verifying") {
    percent = Number(job.phase_percent ?? 0);
    text = `SHA-256 校验 ${percent.toFixed(0)}%`;
  } else if (job.state === "extracting") {
    percent = job.phase_percent == null ? 0 : Number(job.phase_percent);
    text = job.phase_percent == null ? "解压中…" : `解压 ${percent.toFixed(0)}%`;
  } else if (job.state === "done") {
    percent = 100;
    text = "安装完成";
  } else if (job.state === "error") {
    percent = Number(job.percent || 0);
    text = `失败：${job.error || job.message}`;
  } else if (job.state === "cancelled") {
    percent = Number(job.percent || 0);
    text = job.message || "已取消";
  }

  $("#engine-progress-fill").style.width = `${Math.max(0, Math.min(100, percent))}%`;
  $("#engine-progress-text").textContent = text;
  cancel.hidden = !job.active;
  button.disabled = Boolean(job.active);
  if (job.active) {
    button.textContent = "正在安装…";
  } else if (job.state === "error" || job.state === "cancelled") {
    button.textContent = "重试（续传）";
  } else {
    button.textContent = "下载并安装";
  }
}

function renderComponentsPanel() {
  const info = ui.components;
  const box = $("#engine-install");
  if (!info) {
    box.hidden = true;
    return;
  }
  const job = ui.job || info.job;
  const gptJob = job && String(job.component || "").startsWith("gpt") ? job : null;
  const active = Boolean(gptJob && gptJob.active);
  const installed = Boolean(info.gpt_sovits && info.gpt_sovits.available) && !active;
  box.hidden = installed;
  if (installed) return;

  populatePackages(info);
  if (!$("#engine-target").value) $("#engine-target").value = info.default_dir || "";
  updatePackageMeta();

  const extractor = info.extractor;
  const hint = $("#engine-install-hint");
  if (active) {
    hint.textContent = "安装中";
    hint.className = "pill warn";
  } else if (!extractor || !extractor.available) {
    hint.textContent = "缺少解压工具";
    hint.className = "pill bad";
  } else {
    hint.textContent = `解压：${extractor.name}`;
    hint.className = "pill subtle";
  }
  renderJob(gptJob);
  renderUninstallButtons();
}

function stopPolling() {
  if (ui.pollTimer) {
    clearInterval(ui.pollTimer);
    ui.pollTimer = null;
  }
}

function applyJob(job) {
  const previous = ui.job ? ui.job.state : null;
  ui.job = job;
  const isRvc = Boolean(job && String(job.component || "").startsWith("rvc"));
  renderComponentsPanel();
  renderRvcSetup();
  if (!job || !job.active) {
    stopPolling();
    if (job && previous && previous !== job.state) {
      if (job.state === "done") {
        toast(
          isRvc
            ? "RVC 换声引擎已安装并启用"
            : job.slim
              ? "GPT-SoVITS 已安装并启用（精简版）"
              : "GPT-SoVITS 已安装并启用",
        );
        loadState();
        loadComponents();
        loadStorage(true);
      } else if (job.state === "error") {
        showError(job.error || job.message || "安装失败");
        toast(isRvc ? "RVC 安装失败" : "GPT-SoVITS 安装失败", "bad");
      } else if (job.state === "cancelled") {
        toast("已取消，可重试续传");
      }
    }
  }
}

function startPolling() {
  renderComponentsPanel();
  if (ui.pollTimer) return;
  ui.pollTimer = setInterval(async () => {
    try {
      const payload = await api("/api/components/job");
      applyJob(payload.job);
    } catch (error) {
      stopPolling();
    }
  }, 1000);
}

async function startDownload() {
  const item = currentPackage();
  if (!ui.components || !item) {
    toast("组件清单尚未加载", "bad");
    return;
  }
  await requestPreflight({
    id: item.id,
    target: $("#engine-target").value.trim(),
    source: $("#engine-source").value,
    keep_archive: $("#engine-keep-archive").checked,
    slim: Boolean($("#engine-slim")?.checked),
  });
}

async function cancelDownload() {
  try {
    const payload = await api("/api/components/cancel", { method: "POST", body: "{}" });
    applyJob(payload.job);
    toast("正在取消…");
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function startRvcDownload() {
  if (!ui.components) {
    toast("组件清单尚未加载", "bad");
    return;
  }
  const id = ui.components.rvc_component || "rvc-nvidia";
  await requestPreflight({
    id,
    target: "",
    source: "auto",
    keep_archive: false,
    slim: false,
  });
}

function scrollToSetupTarget(selector) {
  const target = document.querySelector(selector);
  if (!target) return;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.remove("setup-flash");
  void target.offsetWidth;
  target.classList.add("setup-flash");
  setTimeout(() => target.classList.remove("setup-flash"), 1400);
}

async function browseFolder(inputSelector, title) {
  const input = $(inputSelector);
  try {
    const payload = await api("/api/dialog/folder", {
      method: "POST",
      body: JSON.stringify({ title, initial: input.value.trim() }),
    });
    if (payload.path) input.value = payload.path;
  } catch (error) {
    toast(error.message, "bad");
  }
}

function renderBanks(data) {
  const summary = data.banks || {};
  const bankList = data.bank_list || [];
  const total = summary.total || 0;

  $("#bank-count-pill").textContent = `${total} 个 bank`;
  $("#bank-size-pill").textContent = `${summary.total_size_mb || 0} MB`;
  $("#all-bank-count").textContent = String(bankList.length);

  const crewLanguages = summary.crew_languages || [];
  const crewVariants = summary.crew_variants || [];
  const keyBanks = summary.key_banks || [];
  const categories = summary.category_counts || {};
  const presentKeys = keyBanks.filter((item) => item.present).length;

  $("#bank-summary").innerHTML = `
    <div class="stat"><span class="stat-label">总 bank</span><span class="stat-value">${total}</span></div>
    <div class="stat"><span class="stat-label">总大小</span><span class="stat-value">${summary.total_size_mb || 0} MB</span></div>
    <div class="stat"><span class="stat-label">乘员语种</span><span class="stat-value">${crewLanguages.length}</span></div>
    <div class="stat"><span class="stat-label">关键 bank</span><span class="stat-value">${presentKeys}/${keyBanks.length || 0}</span></div>
  `;

  $("#key-bank-body").innerHTML = keyBanks.length
    ? keyBanks
        .map(
          (item) => `
          <tr>
            <td class="mono">${escapeHtml(item.name)}</td>
            <td>${escapeHtml(item.label)}</td>
            <td>${item.present ? `${item.size_mb} MB` : "—"}</td>
            <td><span class="pill ${item.present ? "ok" : "subtle"}">${item.present ? "存在" : "缺失"}</span></td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="4" class="muted">选择战雷目录后显示</td></tr>`;

  const crewHtml = crewLanguages.length
    ? crewLanguages
        .map((item) => {
          const tags = [item.common ? "通用" : "", item.ground ? "地面" : ""].filter(Boolean).join(" / ");
          return `<span class="chip ok">${escapeHtml(item.label)}<span class="tag">${escapeHtml(tags)}</span></span>`;
        })
        .join("")
    : `<span class="muted small">选择战雷目录后显示</span>`;
  const variantHtml = crewVariants.length
    ? `<div class="hint">备用录音：${crewVariants.map((item) => escapeHtml(item.code)).join(" / ")}</div>`
    : "";
  $("#crew-langs").innerHTML = crewHtml + variantHtml;

  $("#category-counts").innerHTML = Object.keys(categories).length
    ? Object.entries(categories)
        .map(([label, count]) => `<span class="chip">${escapeHtml(label)}<span class="tag">${count}</span></span>`)
        .join("")
    : `<span class="muted small">选择战雷目录后显示</span>`;

  $("#all-bank-body").innerHTML = bankList.length
    ? bankList
        .map(
          (item) => `
          <tr>
            <td class="mono">${escapeHtml(item.name)}</td>
            <td>${escapeHtml(item.category_label)}</td>
            <td>${escapeHtml(item.language_label || "全局")}</td>
            <td>${formatMb(item.size_bytes)}</td>
            <td>${item.has_meta ? "有" : "缺"}</td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="5" class="muted">选择战雷目录后显示</td></tr>`;
}

async function selectGame(path) {
  if (!path) {
    toast("请先填入战雷目录", "bad");
    return;
  }
  try {
    await api("/api/game/select", { method: "POST", body: JSON.stringify({ path }) });
    toast("已选择战雷目录");
    await loadState();
  } catch (error) {
    showError(error.message);
    toast(error.message, "bad");
  }
}

async function selectKit(path) {
  if (!path) {
    toast("请先填入官方 FMOD 工程目录", "bad");
    return;
  }
  try {
    await api("/api/kit/select", { method: "POST", body: JSON.stringify({ path }) });
    toast("已保存 FMOD 工程目录");
    await loadState();
  } catch (error) {
    showError(error.message);
    toast(error.message, "bad");
  }
}

async function selectEngine(path) {
  if (!path) {
    toast("请先填入 GPT-SoVITS 目录", "bad");
    return;
  }
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ gpt_sovits_path: path }),
    });
    toast("已保存 GPT-SoVITS 目录");
    await loadState();
    await loadComponents();
  } catch (error) {
    showError(error.message);
    toast(error.message, "bad");
  }
}

const TASK_KIND_LABELS = {
  train: "训练音色",
  rvc_train: "训练换声模型",
  synthesize: "合成语音",
  voice_convert: "原语言换声",
  build: "构建语音包",
  preview: "生成试听",
  quick_prepare: "准备语音包",
  catalog: "建立素材目录",
  unpack: "解包 bank",
};

const TASK_KIND_LINKS = {
  train: "#workbench",
  rvc_train: "#workbench",
  preview: "#workbench",
  synthesize: "#voices",
  voice_convert: "#workbench",
  build: "#voices",
  quick_prepare: "#workbench",
  catalog: "#assets",
  unpack: "#assets",
};

let taskDockTimer = null;
let taskDockBusy = false;

function renderTaskDock(job) {
  const dock = $("#task-dock");
  if (!dock) return;
  if (!job || !job.active) {
    dock.hidden = true;
    return;
  }
  dock.hidden = false;
  const unknown = job.percent == null;
  const percent = Math.max(0, Math.min(100, Number(job.percent ?? 0)));
  $("#task-dock-kind").textContent = TASK_KIND_LABELS[job.kind] || job.kind || "任务";
  $("#task-dock-percent").textContent = unknown ? "进行中" : `${percent.toFixed(0)}%`;
  const bar = $("#task-dock .progress-bar");
  if (bar) bar.classList.toggle("indeterminate", unknown);
  $("#task-dock-fill").style.width = unknown ? "" : `${percent}%`;
  $("#task-dock-msg").textContent = job.message || "处理中…";
  const meta = [];
  if (job.subject) meta.push(job.subject);
  if (job.stage) meta.push(job.stage);
  if (job.elapsed != null) meta.push(`已用 ${formatEta(job.elapsed)}`);
  $("#task-dock-meta").textContent = meta.join(" · ");
  $("#task-dock-open").href = TASK_KIND_LINKS[job.kind] || "#workbench";
}

async function pollTaskDock() {
  if (taskDockBusy) return;
  taskDockBusy = true;
  try {
    const data = await api("/api/jobs/current");
    renderTaskDock(data.job);
  } catch {
    /* 服务重启或页面切换瞬间失败时保持现状，下一轮再试 */
  } finally {
    taskDockBusy = false;
  }
}

function bindTaskDock() {
  $("#task-dock-cancel")?.addEventListener("click", async () => {
    try {
      await api("/api/jobs/current/cancel", { method: "POST", body: "{}" });
    } catch (error) {
      toast(error.message, "bad");
    }
    pollTaskDock();
  });
  pollTaskDock();
  clearInterval(taskDockTimer);
  taskDockTimer = setInterval(pollTaskDock, 2000);
}

const lineTuningState = {
  projectId: "",
  project: null,
  line: null,
  text: "",
  scope: [],
  original: false,
  audio: null,
  previewing: false,
};

const tuningSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function tuningReferenceFiles(project) {
  const files = Array.isArray(project?.reference?.files) ? project.reference.files : [];
  const texts = project?.reference?.texts && typeof project.reference.texts === "object"
    ? project.reference.texts
    : {};
  return files.map((name) => ({
    name,
    text: String(texts[name] || "").trim(),
  }));
}

function tuningRefLabel(item, index) {
  const text = item.text.length > 22 ? `${item.text.slice(0, 22)}…` : item.text;
  return `${String(index + 1).padStart(2, "0")}${text ? ` · ${text}` : " · 未填参考文字"}`;
}

function renderTuningReference() {
  const select = $("#line-tuning-ref");
  const hint = $("#line-tuning-ref-text");
  if (!select) return;
  const files = tuningReferenceFiles(lineTuningState.project);
  const selected = files.find((item) => item.name === select.value);
  if (hint) {
    if (!select.value) {
      const defaultName = lineTuningState.project?.reference?.prompt_file || files[0]?.name || "";
      hint.textContent = defaultName
        ? `使用项目默认参考：${defaultName}`
        : "项目里还没有参考音频，无法生成语音。";
    } else if (!selected?.text) {
      hint.textContent = "这段参考还没有对应文字，合成时会用项目参考文字代替，语气可能不稳定。";
    } else {
      hint.textContent = `参考文字：“${selected.text.slice(0, 60)}${selected.text.length > 60 ? "…" : ""}”`;
    }
  }
  const play = $("#line-tuning-ref-play");
  if (play) play.disabled = !files.length;
}

function openLineTuning(options = {}) {
  const dialog = $("#line-tuning-dialog");
  const project = options.project;
  const line = options.line;
  if (!dialog || !project || !line) return;
  lineTuningState.projectId = project.id;
  lineTuningState.project = project;
  lineTuningState.line = line;
  lineTuningState.text = String(options.text ?? line.text ?? "").trim();
  lineTuningState.scope = Array.isArray(options.scope) ? options.scope.filter(Boolean) : [];
  lineTuningState.original = Boolean(options.original);
  if (lineTuningState.audio) {
    lineTuningState.audio.pause();
    lineTuningState.audio = null;
  }

  const source = line.source || {};
  const label = source.description || source.event || line.target_file || "这句台词";
  const excerpt = lineTuningState.text
    ? ` · ${lineTuningState.text.slice(0, 48)}${lineTuningState.text.length > 48 ? "…" : ""}`
    : "";
  $("#line-tuning-line").textContent = `${label}${excerpt}`;

  const files = tuningReferenceFiles(project);
  const tuning = line.tuning && typeof line.tuning === "object" ? line.tuning : {};
  const select = $("#line-tuning-ref");
  select.innerHTML =
    '<option value="">项目默认参考</option>' +
    files
      .map(
        (item, index) =>
          `<option value="${escapeHtml(item.name)}">${escapeHtml(tuningRefLabel(item, index))}</option>`,
      )
      .join("");
  const currentRef = String(tuning.reference || "");
  select.value = files.some((item) => item.name === currentRef) ? currentRef : "";
  $("#line-tuning-ref-count").textContent = files.length ? `${files.length} 段参考` : "没有参考音频";
  renderTuningReference();

  const speed = Math.max(0.75, Math.min(1.35, Number(tuning.speed ?? 1)));
  const gain = Math.max(-12, Math.min(6, Number(tuning.gain_db ?? 0)));
  $("#line-tuning-speed").value = String(speed);
  $("#line-tuning-gain").value = String(gain);
  $("#line-tuning-speed-value").textContent = `${speed.toFixed(2)}×`;
  $("#line-tuning-gain-value").textContent = `${gain > 0 ? "+" : ""}${gain.toFixed(1)} dB`;
  $("#line-tuning-ref-field").hidden = lineTuningState.original;
  $("#line-tuning-speed-field").hidden = lineTuningState.original;
  $("#line-tuning-note").textContent = lineTuningState.original
    ? "原语言换声会保留原语气；这里只能调整音量，改完立刻套用到已换声的音频。"
    : "改动参考语气或语速后，这一句需要重新生成；只改音量不会丢现有音频。";
  const preview = $("#line-tuning-preview");
  preview.disabled = lineTuningState.original || !lineTuningState.text || lineTuningState.previewing;
  preview.textContent = "试听这句";
  const bulkRow = $("#line-tuning-bulk-row");
  const bulk = $("#line-tuning-bulk");
  const scopeIds = [...new Set(lineTuningState.scope)];
  bulk.checked = false;
  bulkRow.hidden = scopeIds.length <= 1;
  $("#line-tuning-bulk-count").textContent = String(scopeIds.length);
  const save = $("#line-tuning-save");
  save.disabled = false;
  save.textContent = "保存设置";
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

async function saveLineTuning() {
  const state = lineTuningState;
  if (!state.projectId || !state.line) return;
  const scopeIds = [...new Set(state.scope)];
  const bulk = Boolean($("#line-tuning-bulk")?.checked) && scopeIds.length > 1;
  const lines = bulk ? scopeIds : [state.line.id];
  const reference = $("#line-tuning-ref").value;
  const speed = Number($("#line-tuning-speed").value || 1);
  const gain = Number($("#line-tuning-gain").value || 0);
  const payload = {
    project: state.projectId,
    lines,
    gain_db: gain,
  };
  if (!state.original) {
    payload.reference = reference;
    payload.speed = speed;
  }
  const button = $("#line-tuning-save");
  button.disabled = true;
  button.textContent = "保存中…";
  try {
    const data = await api("/api/voice/line/tuning", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#line-tuning-dialog")?.close();
    const needs = Number(data.needs_synthesis || 0);
    const updated = Number(data.updated || 0);
    const capped = Number(data.capped || 0);
    let message = state.original
      ? `音量已应用到 ${updated} 句`
      : needs
        ? `已保存：${needs} 句语气/语速变了，需要重新生成`
        : updated > 0
          ? `已保存，${updated} 句设置已更新`
          : "设置已保存";
    if (capped) message += `；${capped} 句音量已限制以避免削波`;
    toast(message);
    window.dispatchEvent(
      new CustomEvent("wt:line-tuning-saved", {
        detail: {
          project: data.project,
          needsSynthesis: needs,
          updated,
        },
      }),
    );
  } catch (error) {
    toast(error.message, "bad");
    button.disabled = false;
    button.textContent = "保存设置";
  }
}

async function previewLineTuning() {
  const state = lineTuningState;
  if (!state.projectId || !state.line || !state.text || state.previewing) return;
  const button = $("#line-tuning-preview");
  state.previewing = true;
  button.disabled = true;
  button.textContent = "试听生成中…";
  try {
    const payload = {
      project: state.projectId,
      text: state.text,
      reference: $("#line-tuning-ref").value,
      speed: Number($("#line-tuning-speed").value || 1),
      gain_db: Number($("#line-tuning-gain").value || 0),
    };
    const started = await api("/api/voice/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    let job = started.job;
    const jobId = job?.id || "";
    const deadline = Date.now() + 6 * 60 * 1000;
    while (job && job.active && Date.now() < deadline) {
      await tuningSleep(1200);
      const poll = await api("/api/jobs/current");
      job = poll.job;
      if (job && job.id !== jobId) {
        throw new Error("有另一个任务开始了，试听已中止");
      }
    }
    if (!job || job.state !== "done" || !job.result?.url) {
      throw new Error(job?.error || "试听生成失败");
    }
    if (state.audio) state.audio.pause();
    state.audio = new Audio(job.result.url);
    await state.audio.play().catch(() => {
      throw new Error("试听已生成，但播放失败");
    });
    toast("正在播放这句的试听");
    button.textContent = "试听这句";
  } catch (error) {
    toast(error.message, "bad");
    button.textContent = "试听这句";
  } finally {
    state.previewing = false;
    button.disabled = state.original || !state.text;
  }
}

function playTuningReference() {
  const state = lineTuningState;
  if (!state.project) return;
  const files = tuningReferenceFiles(state.project);
  const selected = $("#line-tuning-ref")?.value || "";
  const name =
    selected ||
    state.project.reference?.prompt_file ||
    files[0]?.name ||
    "";
  if (!name) {
    toast("项目里还没有参考音频", "bad");
    return;
  }
  if (state.audio) state.audio.pause();
  const url =
    `/api/voice/reference/audio?project=${encodeURIComponent(state.projectId)}` +
    `&file=${encodeURIComponent(name)}`;
  state.audio = new Audio(url);
  state.audio.play().catch(() => toast("参考音频播放失败", "bad"));
}

window.WTLineTuning = { open: openLineTuning };

function bindActions() {
  $(".nav-item[data-route='setup']")?.addEventListener("click", dismissSetupHint);
  $("#refresh-btn")?.addEventListener("click", () => {
    loadState();
    loadComponents();
    loadStorage(true);
    window.dispatchEvent(new CustomEvent("wt:refresh-wizard"));
  });
  $("#setup-refresh-btn")?.addEventListener("click", () => {
    loadState();
    loadComponents();
    loadStorage(true);
  });
  $("#storage-refresh-btn")?.addEventListener("click", () => {
    toast("正在重新统计磁盘占用");
    loadStorage(true);
  });
  $("#storage-clean-btn")?.addEventListener("click", openCleanupDialog);
  $("#storage-clean-cancel")?.addEventListener("click", () =>
    $("#storage-clean-dialog")?.close(),
  );
  $("#storage-clean-start")?.addEventListener("click", startCleanup);
  $("#setup-gpt-uninstall")?.addEventListener("click", () => openComponentUninstall("gpt"));
  $("#setup-rvc-uninstall")?.addEventListener("click", () => openComponentUninstall("rvc"));
  $("#component-uninstall-cancel")?.addEventListener("click", () =>
    $("#component-uninstall-dialog")?.close(),
  );
  $("#component-uninstall-start")?.addEventListener("click", startComponentUninstall);
  $("#setup-gpt-btn")?.addEventListener("click", () => {
    const panel = $("#ai-engine");
    if (panel) panel.open = true;
    scrollToSetupTarget("#ai-engine");
    $("#engine-install")?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#open-uninstall")?.addEventListener("click", openUninstallDialog);
  $("#uninstall-cancel")?.addEventListener("click", () => $("#uninstall-dialog")?.close());
  $("#uninstall-keep-data")?.addEventListener("change", syncUninstallChoice);
  $("#uninstall-start")?.addEventListener("click", startUninstall);
  $("#setup-rvc-btn")?.addEventListener("click", startRvcDownload);
  $("#setup-rvc-cancel")?.addEventListener("click", cancelDownload);
  $("#setup-game-btn")?.addEventListener("click", () => {
    const panel = $("#setup-panel");
    if (panel) panel.open = true;
    scrollToSetupTarget("#game-path");
    setTimeout(() => $("#game-path")?.focus(), 350);
  });
  $("#setup-kit-btn")?.addEventListener("click", () => {
    const panel = $("#setup-panel");
    if (panel) panel.open = true;
    scrollToSetupTarget("#kit-path");
    setTimeout(() => $("#kit-path")?.focus(), 350);
  });
  $("#setup-checks")?.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-setup-jump]");
    if (chip) scrollToSetupTarget(`#${chip.dataset.setupJump}`);
  });
  $("#game-use-btn").addEventListener("click", () => selectGame($("#game-path").value.trim()));
  $("#kit-use-btn").addEventListener("click", () => selectKit($("#kit-path").value.trim()));
  $("#engine-use-btn").addEventListener("click", () => selectEngine($("#engine-path").value.trim()));

  $("#game-browse-btn").addEventListener("click", () => browseFolder("#game-path", "选择战雷安装目录"));
  $("#kit-browse-btn").addEventListener("click", () => browseFolder("#kit-path", "选择官方 FMOD 工程目录"));
  $("#engine-browse-btn").addEventListener("click", () => browseFolder("#engine-path", "选择 GPT-SoVITS 主目录"));
  $("#engine-target-browse").addEventListener("click", () =>
    browseFolder("#engine-target", "选择组件安装位置"),
  );
  $("#engine-package").addEventListener("change", updatePackageMeta);
  $("#engine-slim")?.addEventListener("change", updatePackageMeta);
  $("#engine-download-btn").addEventListener("click", startDownload);
  $("#engine-cancel-btn").addEventListener("click", cancelDownload);
  $("#install-preflight-cancel")?.addEventListener("click", () => {
    ui.pendingInstall = null;
    $("#install-preflight-dialog")?.close();
  });
  $("#install-preflight-start")?.addEventListener("click", startPreflightDownload);
  $("#line-tuning-cancel")?.addEventListener("click", () =>
    $("#line-tuning-dialog")?.close(),
  );
  $("#line-tuning-dialog")?.addEventListener("close", () => {
    if (lineTuningState.audio) lineTuningState.audio.pause();
    lineTuningState.audio = null;
  });
  $("#line-tuning-save")?.addEventListener("click", saveLineTuning);
  $("#line-tuning-preview")?.addEventListener("click", previewLineTuning);
  $("#line-tuning-ref")?.addEventListener("change", renderTuningReference);
  $("#line-tuning-ref-play")?.addEventListener("click", playTuningReference);
  $("#line-tuning-speed")?.addEventListener("input", (event) => {
    $("#line-tuning-speed-value").textContent = `${Number(event.target.value).toFixed(2)}×`;
  });
  $("#line-tuning-gain")?.addEventListener("input", (event) => {
    const gain = Number(event.target.value);
    $("#line-tuning-gain-value").textContent = `${gain > 0 ? "+" : ""}${gain.toFixed(1)} dB`;
  });

  $("#game-installs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-select-game]");
    if (button) selectGame(button.dataset.selectGame);
  });

  $("#kit-candidates").addEventListener("click", (event) => {
    const button = event.target.closest("[data-select-kit]");
    if (button) selectKit(button.dataset.selectKit);
  });

  document.querySelectorAll("[data-open-folder]").forEach((button) => {
    button.addEventListener("click", () => openFolder(button.dataset.openFolder));
  });
  document.querySelectorAll("[data-copy-path]").forEach((button) => {
    button.addEventListener("click", () => copyPath(button.dataset.copyPath));
  });
}

window.addEventListener("hashchange", routePages);

document.addEventListener("DOMContentLoaded", () => {
  routePages();
  bindActions();
  bindTaskDock();
  loadState().then(() => {
    loadComponents();
    loadStorage();
  });
});
