/* M1：事件目录、源素材试听、bank 解包与覆盖率 */
(() => {
  "use strict";

  const state = {
    status: null,
    query: null,
    unpack: null,
    manifest: null,
    bank: "",
    filters: { bank: "", category: "", language: "", has_assets: "", q: "" },
    page: 1,
    pageSize: 50,
    sort: "bank",
    order: "asc",
    streamPage: 1,
    streamPageSize: 50,
    job: null,
    pollTimer: null,
    pollBusy: false,
    refreshing: false,
    queued: false,
    initialized: false,
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
    if (!response.ok) {
      const error = new Error(payload.error || `请求失败（${response.status}）`);
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function toastMessage(message, tone = "ok") {
    if (typeof toast === "function") {
      toast(message, tone);
      return;
    }
    console.log(message);
  }

  function errorMessage(message) {
    if (typeof showError === "function") {
      showError(message);
      return;
    }
    console.error(message);
  }

  function setPill(id, text, tone = "") {
    const node = el(id);
    if (!node) return;
    node.textContent = text;
    node.className = `pill ${tone}`.trim();
  }

  function formatSeconds(value) {
    const seconds = Number(value || 0);
    if (!Number.isFinite(seconds) || seconds <= 0) return "—";
    if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)}s`;
    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);
    return `${minutes}:${String(rest).padStart(2, "0")}`;
  }

  function formatHours(value) {
    const seconds = Number(value || 0);
    if (!Number.isFinite(seconds) || seconds <= 0) return "—";
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "—";
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  function formatDate(value) {
    if (!value) return "—";
    return String(value).replace("T", " ").slice(0, 19);
  }

  function setOptions(select, items, allLabel, current) {
    if (!select) return;
    const signature = `${allLabel}::${items.map((item) => `${item.value}=${item.label}`).join("|")}`;
    if (select.dataset.signature !== signature) {
      select.innerHTML =
        `<option value="">${escape(allLabel)}</option>` +
        items
          .map(
            (item) =>
              `<option value="${escape(item.value)}">${escape(item.label)}</option>`,
          )
          .join("");
      select.dataset.signature = signature;
    }
    let value = current || "";
    if (value && !items.some((item) => item.value === value)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    }
    select.value = value;
  }

  // ---------- 状态渲染 ----------

  function renderCatalogStatus() {
    const status = state.status;
    const job = state.job && state.job.kind === "catalog" ? state.job : null;
    const active = Boolean(job && job.active);
    const box = el("catalog-state");
    const body = el("catalog-body");
    const progress = el("catalog-build-wrap");
    const fill = el("catalog-build-fill");
    const text = el("catalog-build-text");
    const bar = progress ? progress.querySelector(".progress-bar") : null;

    if (!status) {
      setPill("catalog-pill", "读取中", "subtle");
      box.hidden = false;
      box.innerHTML = `<p class="muted">正在读取目录状态…</p>`;
      body.hidden = true;
      progress.hidden = true;
      el("catalog-rebuild-btn").hidden = true;
      return;
    }

    el("catalog-rebuild-btn").hidden = !status.available || active;
    el("catalog-rebuild-btn").disabled = active;

    const noKit = status.reason === "未选择官方 FMOD 工程";
    if (noKit) {
      setPill("catalog-pill", "未选择工程");
    } else if (active) {
      setPill("catalog-pill", "生成中", "warn");
    } else if (!status.available) {
      setPill("catalog-pill", "待生成", "warn");
    } else if (status.stale) {
      setPill("catalog-pill", "需重建", "warn");
    } else {
      setPill("catalog-pill", "已就绪", "ok");
    }

    if (active) {
      progress.hidden = false;
      if (bar) bar.classList.toggle("indeterminate", job.percent == null);
      fill.style.width = job.percent == null ? "35%" : `${Math.max(0, Math.min(100, job.percent))}%`;
      const base = job.message || "生成中";
      let detail = "";
      if (job.stage === "assets" && job.current) {
        detail = ` · ${job.current} 个文件`;
      } else if (job.total && !base.includes("/")) {
        detail = ` · ${job.current}/${job.total}`;
      }
      text.textContent = base + detail;
    } else {
      progress.hidden = true;
    }

    const showBody = Boolean(status.available);
    body.hidden = !showBody;

    if (active) {
      box.hidden = false;
      box.innerHTML = showBody
        ? `<div class="empty-title">正在重新生成事件目录</div><p>可以继续浏览旧缓存；完成后会自动刷新。</p>`
        : `<div class="empty-title">正在生成事件目录</div><p>首次约 20–60 秒，会读取 12,256 个事件和素材元数据。生成期间不影响其他操作。</p>`;
      return;
    }

    if (noKit) {
      box.hidden = false;
      box.innerHTML = `<div class="empty-title">还没有可解析的官方工程</div><p>请先在上方选择 Gaijin 官方 FMOD 工程目录（只读）。</p>`;
      return;
    }

    if (!status.available) {
      box.hidden = false;
      const otherActive = state.job && state.job.active;
      box.innerHTML = `
        <div class="empty-title">还没有事件目录</div>
        <p>${otherActive ? "当前有其他任务在运行，完成后即可生成目录。" : "首次生成会建立“事件 → bank → 素材”索引并缓存到本地，之后查询是秒开。预计 20–60 秒。"}</p>
        <div class="empty-actions">
          <button class="btn" type="button" data-catalog-build="1" ${otherActive ? "disabled" : ""}>生成事件目录</button>
        </div>`;
      return;
    }

    if (status.stale) {
      box.hidden = false;
      box.innerHTML = `
        <div class="empty-title">缓存可能需要更新</div>
        <p>${escape(status.reason || "工程内容可能已变化。")} 旧缓存仍可浏览，也可以重新生成。</p>
        <div class="empty-actions">
          <button class="btn" type="button" data-catalog-build="1" data-force="1">重新生成</button>
        </div>`;
      return;
    }

    box.hidden = true;
    box.innerHTML = "";
  }

  function renderSummary(data) {
    const stats = data.stats || state.status?.stats;
    if (!stats) return;
    const missing = stats.missing_files_assigned ?? stats.missing_files ?? 0;
    const existing = stats.existing_assets_assigned ?? stats.existing_assets ?? 0;
    const unique = stats.unique_assets_assigned ?? stats.unique_assets ?? 0;
    const values = [
      ["事件", stats.assigned_events],
      ["可试听事件", stats.events_with_assets],
      ["素材引用", stats.asset_refs],
      ["缺失素材", missing],
      ["总时长", formatHours(stats.duration_sec)],
    ];
    el("catalog-summary").innerHTML = values
      .map(
        ([label, value]) =>
          `<div class="stat"><span class="stat-label">${escape(label)}</span>` +
          `<span class="stat-value">${escape(value)}</span></div>`,
      )
      .join("");
    const hint = el("catalog-hint");
    if (missing > 0) {
      const totalMissing = stats.missing_files ?? missing;
      const unassigned = Math.max(0, totalMissing - missing);
      hint.textContent =
        `官方工程有 ${unique} 个有 bank 归属的素材，${existing} 个文件存在，${missing} 个缺失` +
        (unassigned ? `（未分配事件另有 ${unassigned} 个）` : "") +
        "。剩余多为效果颗粒/提示音；源码版可用 tools/fill_radio_chat.py 从游戏 bank 补全无线电语音。" +
        "缺失事件仍会列出，但暂时不能试听。";
    } else {
      hint.textContent = "官方工程的素材路径已全部对上，可以直接试听。";
    }
  }

  function populateFilters(facets) {
    const banks = (facets.banks || []).filter((item) => item.event_count > 0);
    setOptions(
      el("catalog-bank"),
      banks.map((item) => ({
        value: item.name,
        label: `${item.name} · ${item.event_count}`,
      })),
      "全部 bank",
      state.filters.bank,
    );
    setOptions(
      el("catalog-category"),
      (facets.categories || []).map((item) => ({
        value: item.key,
        label: `${item.label} · ${item.count}`,
      })),
      "全部分类",
      state.filters.category,
    );
    setOptions(
      el("catalog-language"),
      (facets.languages || []).map((item) => ({
        value: item.key,
        label: `${item.label} · ${item.count}`,
      })),
      "全部语种",
      state.filters.language,
    );
    el("catalog-has-assets").value = state.filters.has_assets;
    el("catalog-page-size").value = String(state.pageSize);
    const search = el("catalog-search");
    if (document.activeElement !== search && search.value !== state.filters.q) {
      search.value = state.filters.q;
    }
  }

  function renderEvents(data) {
    const body = el("catalog-events-body");
    const events = data.events || [];
    if (!events.length) {
      body.innerHTML = `<tr><td colspan="7" class="muted">没有匹配的事件。可以放宽筛选或先生成目录。</td></tr>`;
      return;
    }
    body.innerHTML = events
      .map((event) => {
        const playable = (event.assets || []).find((asset) => asset.exists);
        const count = Number(event.asset_count || 0);
        const existing = Number(event.existing_asset_count || 0);
        let assetPill = `<span class="pill subtle">无引用</span>`;
        if (count && !existing) {
          assetPill = `<span class="pill bad">${count} 缺失</span>`;
        } else if (count) {
          assetPill = `<span class="pill ok">${existing}/${count}</span>`;
        }
        const firstPath = event.assets && event.assets.length ? event.assets[0].path : "";
        const path = firstPath
          ? `<span class="${event.assets[0].exists ? "" : "catalog-missing"}">${escape(firstPath)}</span>`
          : `<span class="muted">—</span>`;
        return `<tr>
          <td class="mono">${escape(event.bank)}</td>
          <td class="catalog-event" title="${escape(event.event)}">${escape(event.event)}</td>
          <td>${escape(event.label || event.category || "—")}</td>
          <td class="mono">${formatSeconds(event.duration_sec)}</td>
          <td>${assetPill}</td>
          <td class="catalog-path" title="${escape(firstPath)}">${path}</td>
          <td>${
            playable
              ? `<button class="catalog-play" type="button" data-play-guid="${escape(playable.guid)}" data-play-label="${escape(`${event.event} · ${playable.path || ""}`)}">播放</button>`
              : `<button class="catalog-play" type="button" disabled>缺失</button>`
          }</td>
        </tr>`;
      })
      .join("");
  }

  function renderPager(data) {
    const page = data.page || { page: 1, pages: 1, total: 0 };
    el("catalog-page-info").textContent =
      `第 ${page.page} / ${page.pages} 页 · 共 ${page.total} 条`;
    el("catalog-prev").disabled = page.page <= 1;
    el("catalog-next").disabled = page.page >= page.pages;
  }

  function renderCoverage(facets) {
    const body = el("catalog-coverage-body");
    const banks = (facets.banks || [])
      .filter((item) => item.event_count > 0)
      .slice()
      .sort((left, right) => right.event_count - left.event_count);
    if (!banks.length) {
      body.innerHTML = `<tr><td colspan="5" class="muted">没有 bank 数据。</td></tr>`;
      return;
    }
    body.innerHTML = banks
      .map((item) => {
        const total = Number(item.event_count || 0);
        const withAssets = Number(item.with_assets || 0);
        const percent = total ? Math.round((withAssets / total) * 100) : 0;
        return `<tr class="coverage-row" data-bank="${escape(item.name)}" title="点击筛选这个 bank">
          <td class="mono">${escape(item.name)}</td>
          <td>${total}</td>
          <td>${withAssets}</td>
          <td>
            <div class="coverage-bar"><div class="bar"><span style="width:${percent}%"></span></div><em>${percent}%</em></div>
          </td>
          <td>${Number(item.missing_assets || 0)}</td>
        </tr>`;
      })
      .join("");
  }

  // ---------- 目录查询 ----------

  async function loadCatalogPage() {
    if (!state.status || !state.status.available) return;
    const sequence = (state.querySeq = (state.querySeq || 0) + 1);
    const params = new URLSearchParams();
    params.set("page", String(state.page));
    params.set("page_size", String(state.pageSize));
    params.set("sort", state.sort);
    params.set("order", state.order);
    Object.entries(state.filters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    try {
      const payload = await apiCall(`/api/catalog/query?${params.toString()}`);
      if (sequence !== state.querySeq) return;
      const result = payload.catalog;
      state.query = result;
      state.page = result.page.page;
      state.pageSize = result.page.page_size;
      populateFilters(result.facets);
      renderSummary(result);
      renderEvents(result);
      renderPager(result);
      renderCoverage(result.facets);
      document.querySelectorAll("#catalog th[data-sort]").forEach((th) => {
        th.classList.toggle("sorted", th.dataset.sort === state.sort);
      });
      document.querySelectorAll("#catalog th[data-sort]").forEach((th) => {
        th.title = th.dataset.sort === state.sort
          ? `当前按 ${state.sort} ${state.order === "asc" ? "升序" : "降序"}，点击切换`
          : "点击排序";
      });
    } catch (error) {
      if (error.payload && error.payload.catalog) {
        state.status = error.payload.catalog;
        renderCatalogStatus();
      }
      throw error;
    }
  }

  // ---------- 解包 ----------

  function currentBank() {
    return (state.unpack?.banks || []).find((item) => item.name === state.bank) || null;
  }

  function bankLabel(item) {
    return `${item.name} · ${formatBytes(item.size_bytes)}` + (item.output ? " · 已解包" : "");
  }

  function renderUnpack() {
    const info = state.unpack;
    if (!info) return;
    const job = state.job && state.job.kind === "unpack" ? state.job : null;
    const active = Boolean(job && job.active);
    const jobBusy = Boolean(state.job && state.job.active);
    const banks = info.banks || [];
    const select = el("unpack-bank");
    const start = el("unpack-start-btn");
    const cancel = el("unpack-cancel-btn");
    const progress = el("unpack-progress");
    const fill = el("unpack-progress-fill");
    const text = el("unpack-progress-text");
    const bar = progress ? progress.querySelector(".progress-bar") : null;

    const vgm = info.vgmstream || {};
    if (vgm.available) {
      el("unpack-vgmstream").innerHTML =
        `<span class="pill ok">vgmstream 就绪</span> ` +
        `${escape(vgm.version || "")}${vgm.bundled ? " · 内置" : ""}`;
    } else {
      el("unpack-vgmstream").innerHTML =
        `<span class="pill warn">vgmstream 未安装</span> 首次解包会自动下载 4.4 MB，` +
        `校验 SHA-256 后再解压。`;
    }

    if (!banks.length) {
      select.innerHTML = `<option value="">先选择战雷目录</option>`;
      select.disabled = true;
      setPill("unpack-pill", "未选择战雷");
      start.disabled = true;
      start.textContent = "解包此 bank";
      cancel.hidden = true;
      progress.hidden = true;
      el("unpack-summary").textContent = "选择战雷目录后，这里会列出可解包的 bank。";
      el("unpack-match-note").textContent = "";
      state.manifest = null;
      renderStreams();
      return;
    }

    if (!state.bank || !banks.some((item) => item.name === state.bank)) {
      const preferred =
        banks.find((item) => item.name === "aircraft_gui") ||
        banks.find((item) => item.output) ||
        banks[0];
      state.bank = preferred ? preferred.name : "";
    }
    const signature = banks
      .map((item) => `${item.name}=${bankLabel(item)}`)
      .join("|");
    if (select.dataset.signature !== signature) {
      select.innerHTML = banks
        .map(
          (item) =>
            `<option value="${escape(item.name)}">${escape(bankLabel(item))}</option>`,
        )
        .join("");
      select.dataset.signature = signature;
    }
    select.disabled = false;
    select.value = state.bank;

    const current = currentBank();
    const output = current && current.output ? current.output : null;
    const counts = output && output.counts ? output.counts : null;

    if (active) {
      setPill("unpack-pill", "解包中", "warn");
    } else if (output) {
      setPill("unpack-pill", "已解包", "ok");
    } else {
      setPill("unpack-pill", "待解包", "subtle");
    }

    start.disabled = jobBusy || !current;
    start.textContent = active
      ? "解包中…"
      : jobBusy
        ? "其他任务进行中"
        : output
          ? "重新解包"
          : "解包此 bank";
    cancel.hidden = !active;

    if (active) {
      progress.hidden = false;
      if (bar) bar.classList.toggle("indeterminate", job.percent == null);
      fill.style.width = job.percent == null ? "35%" : `${Math.max(0, Math.min(100, job.percent))}%`;
      text.textContent =
        `${job.message || "解包中"}` + (job.current ? ` · 已解出 ${job.current} 个流` : "");
    } else {
      progress.hidden = true;
    }

    if (counts) {
      el("unpack-summary").innerHTML =
        `<strong>${Number(counts.streams || 0)}</strong> 个流 · ` +
        `${formatBytes(counts.size_bytes)} · ${formatSeconds(counts.duration_sec)}<br>` +
        `匹配流 ${Number(counts.matched_streams || 0)}/${Number(counts.streams || 0)} · ` +
        `匹配素材 ${Number(counts.matched_assets || 0)}/${Number(counts.catalog_assets || 0)}<br>` +
        `生成于 ${escape(formatDate(output.generated_at))} · vgmstream ${escape(output.vgmstream || "—")}`;
    } else {
      el("unpack-summary").textContent = "这个 bank 还没有解包结果。";
    }

    el("unpack-match-note").textContent = state.status && state.status.available
      ? "解包匹配是 best-effort：游戏内部流名不一定等于官方素材名；权威覆盖率看左侧目录。"
      : "生成事件目录后，解包结果会显示 best-effort 匹配。";
  }

  async function loadManifest(bank, force = false) {
    if (!bank) {
      state.manifest = null;
      renderStreams();
      return;
    }
    const infoBank = (state.unpack?.banks || []).find((item) => item.name === bank);
    if (!infoBank || !infoBank.output) {
      state.manifest = null;
      state.streamPage = 1;
      renderStreams();
      return;
    }
    if (!force && state.manifest && state.manifest.bank === bank) {
      renderStreams();
      return;
    }
    try {
      const payload = await apiCall(`/api/unpack/manifest?bank=${encodeURIComponent(bank)}`);
      state.manifest = payload.manifest;
      state.streamPage = 1;
      renderStreams();
    } catch {
      state.manifest = null;
      renderStreams();
    }
  }

  function renderStreams() {
    const wrap = el("unpack-stream-wrap");
    const body = el("unpack-stream-body");
    const pager = el("unpack-pager");
    const manifest = state.manifest;
    if (!manifest || !manifest.streams || !manifest.streams.length) {
      wrap.hidden = true;
      pager.hidden = true;
      body.innerHTML = "";
      return;
    }
    const streams = manifest.streams;
    const pageSize = state.streamPageSize;
    const pages = Math.max(1, Math.ceil(streams.length / pageSize));
    state.streamPage = Math.min(Math.max(1, state.streamPage), pages);
    const start = (state.streamPage - 1) * pageSize;
    const slice = streams.slice(start, start + pageSize);
    const matchLabels = {
      exact: ["精确", "exact"],
      basename: ["同名", "basename"],
      normalized: ["近似", "normalized"],
      "": ["未匹配", "none"],
    };
    body.innerHTML = slice
      .map((stream) => {
        const [label, tone] = matchLabels[stream.match] || matchLabels[""];
        return `<tr>
          <td class="mono">${escape(stream.id)}</td>
          <td class="stream-name" title="${escape(stream.name)}">${escape(stream.name)}</td>
          <td class="mono">${formatSeconds(stream.duration_sec)}</td>
          <td><span class="match-pill ${tone}" title="${escape(stream.asset || "")}">${label}</span></td>
          <td><button class="catalog-play" type="button" data-play-stream="${escape(stream.id)}" data-play-label="${escape(`${manifest.bank} · ${stream.name}`)}">播放</button></td>
        </tr>`;
      })
      .join("");
    wrap.hidden = false;
    pager.hidden = pages <= 1;
    el("unpack-page-info").textContent = `第 ${state.streamPage} / ${pages} 页 · 共 ${streams.length} 个流`;
    el("unpack-prev").disabled = state.streamPage <= 1;
    el("unpack-next").disabled = state.streamPage >= pages;
  }

  // ---------- 任务 ----------

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function startPolling() {
    if (state.pollTimer) return;
    pollJob();
    state.pollTimer = setInterval(pollJob, 1000);
  }

  async function pollJob() {
    if (state.pollBusy) return;
    state.pollBusy = true;
    try {
      const payload = await apiCall("/api/jobs/current");
      await applyJob(payload.job);
    } catch {
      stopPolling();
    } finally {
      state.pollBusy = false;
    }
  }

  async function applyJob(job) {
    const previous = state.job;
    state.job = job;
    renderCatalogStatus();
    renderUnpack();
    if (job && job.active) {
      startPolling();
      return;
    }
    stopPolling();
    if (!job || !previous || !previous.active || previous.id !== job.id) return;
    if (job.kind === "catalog") {
      if (job.state === "done") {
        toastMessage("事件目录已生成");
        await refreshAll();
      } else if (job.state === "error") {
        errorMessage(job.error || job.message || "生成目录失败");
        await refreshAll();
      } else if (job.state === "cancelled") {
        toastMessage("已取消生成目录");
        await refreshAll();
      }
    } else if (job.kind === "unpack") {
      if (job.state === "done") {
        toastMessage("bank 解包完成");
        await refreshAll();
        await loadManifest(state.bank, true);
      } else if (job.state === "error") {
        errorMessage(job.error || job.message || "解包失败");
        await refreshAll();
      } else if (job.state === "cancelled") {
        toastMessage("已取消解包");
        await refreshAll();
      }
    }
  }

  async function startBuild(force) {
    try {
      const payload = await apiCall("/api/catalog/build", {
        method: "POST",
        body: JSON.stringify({ force: Boolean(force) }),
      });
      if (payload.cached) {
        toastMessage("目录已是最新");
        await refreshAll();
        return;
      }
      state.job = payload.job;
      renderCatalogStatus();
      if (payload.job && payload.job.active) {
        toastMessage("开始生成事件目录");
        startPolling();
      } else {
        await refreshAll();
      }
    } catch (error) {
      errorMessage(error.message);
      toastMessage(error.message, "bad");
    }
  }

  async function startUnpack() {
    const bank = state.bank;
    if (!bank) return;
    const current = currentBank();
    try {
      const payload = await apiCall("/api/unpack/start", {
        method: "POST",
        body: JSON.stringify({ bank, force: Boolean(current && current.output) }),
      });
      state.job = payload.job;
      renderUnpack();
      if (payload.job && payload.job.active) {
        startPolling();
      } else {
        await refreshAll();
      }
    } catch (error) {
      errorMessage(error.message);
      toastMessage(error.message, "bad");
    }
  }

  async function cancelJob() {
    try {
      const payload = await apiCall("/api/jobs/current/cancel", {
        method: "POST",
        body: "{}",
      });
      await applyJob(payload.job);
      toastMessage("正在取消…");
    } catch (error) {
      toastMessage(error.message, "bad");
    }
  }

  // ---------- 总刷新 ----------

  async function refreshAll(options = {}) {
    if (state.refreshing) {
      state.queued = true;
      return;
    }
    state.refreshing = true;
    try {
      const status = await apiCall(
        `/api/catalog/status${options.recheck ? "?recheck=1" : ""}`,
      );
      state.status = status;
      let unpack = null;
      try {
        unpack = await apiCall("/api/unpack");
      } catch {
        unpack = null;
      }
      state.unpack = unpack;
      renderCatalogStatus();
      renderUnpack();
      if (status.available) {
        await loadCatalogPage();
      } else {
        state.query = null;
      }
      if (unpack && state.bank) {
        await loadManifest(state.bank);
      }
      if (status.job && status.job.active) {
        state.job = status.job;
        renderCatalogStatus();
        renderUnpack();
        startPolling();
      }
    } catch (error) {
      errorMessage(error.message);
      const box = el("catalog-state");
      box.hidden = false;
      box.innerHTML = `
        <div class="empty-title">读取目录失败</div>
        <p>${escape(error.message)}</p>
        <div class="empty-actions">
          <button class="btn" type="button" data-catalog-retry="1">重试</button>
        </div>`;
    } finally {
      state.refreshing = false;
      if (state.queued) {
        state.queued = false;
        setTimeout(refreshAll, 50);
      }
    }
  }

  // ---------- 播放 ----------

  function playUrl(url, label) {
    const audio = el("catalog-player");
    audio.src = url;
    el("catalog-player-label").textContent = label || "正在播放";
    audio.play().catch(() => toastMessage("浏览器无法播放这个音频", "bad"));
  }

  // ---------- 事件绑定 ----------

  function bindFilter(id, key) {
    const node = el(id);
    if (!node) return;
    node.addEventListener("change", () => {
      state.filters[key] = node.value;
      state.page = 1;
      loadCatalogPage().catch((error) => {
        errorMessage(error.message);
        toastMessage(error.message, "bad");
      });
    });
  }

  function bindCatalogEvents() {
    el("catalog-refresh-btn").addEventListener("click", () => refreshAll({ recheck: true }));
    el("catalog-rebuild-btn").addEventListener("click", () => startBuild(true));
    el("catalog-build-cancel").addEventListener("click", cancelJob);

    el("catalog-state").addEventListener("click", (event) => {
      const retry = event.target.closest("[data-catalog-retry]");
      if (retry) {
        refreshAll();
        return;
      }
      const build = event.target.closest("[data-catalog-build]");
      if (build) startBuild(build.dataset.force === "1");
    });

    bindFilter("catalog-bank", "bank");
    bindFilter("catalog-category", "category");
    bindFilter("catalog-language", "language");
    bindFilter("catalog-has-assets", "has_assets");

    let searchTimer = null;
    el("catalog-search").addEventListener("input", (event) => {
      clearTimeout(searchTimer);
      const value = event.target.value.trim();
      searchTimer = setTimeout(() => {
        state.filters.q = value;
        state.page = 1;
        loadCatalogPage().catch((error) => toastMessage(error.message, "bad"));
      }, 300);
    });

    el("catalog-page-size").addEventListener("change", (event) => {
      state.pageSize = Number(event.target.value) || 50;
      state.page = 1;
      loadCatalogPage().catch((error) => toastMessage(error.message, "bad"));
    });

    el("catalog-prev").addEventListener("click", () => {
      if (state.page > 1) {
        state.page -= 1;
        loadCatalogPage().catch((error) => toastMessage(error.message, "bad"));
      }
    });
    el("catalog-next").addEventListener("click", () => {
      if (state.query && state.page < state.query.page.pages) {
        state.page += 1;
        loadCatalogPage().catch((error) => toastMessage(error.message, "bad"));
      }
    });

    document.querySelectorAll("#catalog th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const sort = th.dataset.sort;
        if (state.sort === sort) {
          state.order = state.order === "asc" ? "desc" : "asc";
        } else {
          state.sort = sort;
          state.order = "asc";
        }
        state.page = 1;
        loadCatalogPage().catch((error) => toastMessage(error.message, "bad"));
      });
    });

    el("catalog-events-body").addEventListener("click", (event) => {
      const button = event.target.closest("[data-play-guid]");
      if (!button) return;
      playUrl(
        `/api/audio/catalog?guid=${encodeURIComponent(button.dataset.playGuid)}`,
        button.dataset.playLabel,
      );
    });

    el("catalog-coverage-body").addEventListener("click", (event) => {
      const row = event.target.closest("[data-bank]");
      if (!row) return;
      state.filters.bank = row.dataset.bank;
      state.page = 1;
      el("catalog-bank").value = state.filters.bank;
      document.querySelector(".catalog-table-wrap").scrollIntoView({ behavior: "smooth", block: "start" });
      loadCatalogPage().catch((error) => toastMessage(error.message, "bad"));
    });

    el("unpack-bank").addEventListener("change", (event) => {
      state.bank = event.target.value;
      state.manifest = null;
      state.streamPage = 1;
      renderUnpack();
      loadManifest(state.bank, true);
    });
    el("unpack-start-btn").addEventListener("click", startUnpack);
    el("unpack-cancel-btn").addEventListener("click", cancelJob);
    el("unpack-prev").addEventListener("click", () => {
      if (state.streamPage > 1) {
        state.streamPage -= 1;
        renderStreams();
      }
    });
    el("unpack-next").addEventListener("click", () => {
      state.streamPage += 1;
      renderStreams();
    });
    el("unpack-stream-body").addEventListener("click", (event) => {
      const button = event.target.closest("[data-play-stream]");
      if (!button) return;
      playUrl(
        `/api/audio/unpacked?bank=${encodeURIComponent(state.bank)}&stream=${encodeURIComponent(button.dataset.playStream)}`,
        button.dataset.playLabel,
      );
    });
    el("catalog-player").addEventListener("error", () => {
      if (el("catalog-player").src) {
        toastMessage("播放失败：文件可能缺失或浏览器不支持该编码", "bad");
      }
    });
  }

  function init() {
    if (state.initialized) return;
    state.initialized = true;
    bindCatalogEvents();
    window.addEventListener("wt:state", () => {
      refreshAll();
    });
    refreshAll();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
