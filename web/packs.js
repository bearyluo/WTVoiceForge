/* M3.2：成品语音包导入、安装、备份与卸载 */
(() => {
  "use strict";

  const MAX_BYTES = 1024 * 1024 * 1024;
  const ALLOWED = [".wtvoice", ".zip", ".rar", ".7z", ".bank"];

  const state = {
    pack: null,
    game: null,
    installed: null,
    uploading: false,
    installing: false,
    allowMasterbank: false,
    initialized: false,
    contents: null,
    conflicts: null,
    contentsBusy: false,
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
    if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
    return payload;
  }

  function toastMessage(message, tone = "ok") {
    if (typeof toast === "function") toast(message, tone);
    else console.log(message);
  }

  function showError(message) {
    const banner = el("packs-error");
    if (!banner) return;
    banner.textContent = message;
    banner.hidden = !message;
  }

  function setPillById(id, text, tone = "") {
    const node = el(id);
    if (!node) return;
    node.textContent = text;
    node.className = `pill ${tone}`.trim();
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
    if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${value} B`;
  }

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function sourceLabel(type) {
    return {
      wtvoice: "WT Voice Forge 语音包",
      zip: "ZIP 语音包",
      bank: "单个 bank",
    }[type] || type || "语音包";
  }

  function layoutLabel(layout) {
    return {
      sound_mod: "sound/mod 结构",
      mod: "mod 结构",
      banks: "banks/ 旧格式",
      flat: "根目录 bank",
      nested: "多层目录",
      mixed: "混合目录",
      single: "单个 bank",
    }[layout] || "";
  }

  function configState(game) {
    const config = game?.config || {};
    return {
      enable_mod:
        typeof config.enable_mod === "boolean" ? config.enable_mod : game?.enable_mod ?? null,
      fmod_sound_enable:
        typeof config.fmod_sound_enable === "boolean" ? config.fmod_sound_enable : null,
      use_loops_gun_sound: config.use_loops_gun_sound ?? null,
      use_loops_gm_gun_sound: config.use_loops_gm_gun_sound ?? null,
    };
  }

  function shortName(name) {
    const text = String(name || "");
    return text.length > 20 ? `${text.slice(0, 20)}…` : text;
  }

  function bankStatus(conflict) {
    if (!conflict) return { text: "待检查", tone: "subtle" };
    if (conflict.mod_same) return { text: "mod 中已是同一文件", tone: "ok" };
    if (conflict.mod_present) {
      return conflict.owner_name
        ? { text: `将覆盖「${shortName(conflict.owner_name)}」的 bank（先备份）`, tone: "warn" }
        : { text: "将覆盖来源不明的同名 bank（先备份）", tone: "warn" };
    }
    if (conflict.base_present) return { text: "新增到 mod（游戏自带同名）", tone: "ok" };
    return { text: "新增", tone: "ok" };
  }

  function installedStatus(status) {
    return {
      ok: { text: "已安装", tone: "ok" },
      missing: { text: "文件缺失", tone: "warn" },
      overwritten: { text: "已被改动/覆盖", tone: "bad" },
    }[status] || { text: status, tone: "subtle" };
  }

  function gamePill(data) {
    if (!data || !data.valid) return ["未选择游戏", "bad"];
    if (data.running) return ["游戏正在运行", "warn"];
    return ["游戏就绪", "ok"];
  }

  function renderGame(data) {
    state.game = data || state.game;
    const [text, tone] = gamePill(data);
    setPillById("packs-game-pill", text, tone);
    setPillById("pack-game-state", data?.valid ? (data.running ? "先关游戏" : "可安装") : "未就绪", tone);

    const box = el("pack-game-info");
    if (!box) return;
    if (!data || !data.valid) {
      box.innerHTML = `
        <p class="muted small">${escape(data?.reason || "还没有选择战雷目录。")}</p>
        <p class="hint small">先到 <a href="#workbench">工作台</a> 选择 War Thunder 安装目录，再回来安装语音包。</p>
      `;
      return;
    }

    const config = configState(data);
    const enable = config.enable_mod;
    const rows = [
      ["游戏目录", data.path || data.game],
      ["sound/mod", data.mod_dir],
      ["enable_mod", enable === true ? "yes" : enable === false ? "no" : "未写入"],
      ["fmod_sound_enable", config.fmod_sound_enable === true ? "yes" : config.fmod_sound_enable === false ? "no" : "未写入"],
    ];

    box.innerHTML = `
      ${rows
        .map(
          ([key, value]) => `
            <div class="kv-row pack-game-row">
              <span>${escape(key)}</span>
              <span class="pack-game-path">${escape(value)}</span>
            </div>`,
        )
        .join("")}
      ${
        data.running
          ? '<p class="hint small" style="color: var(--warn)">检测到 aces.exe 正在运行，安装/卸载和修改 config.blk 都会被拒绝。</p>'
          : ""
      }
      ${
        enable === true
          ? '<p class="hint small">mod 支持已开启，安装的 bank 会在下次启动游戏时生效。</p>'
          : `<p class="hint small">enable_mod 还不是 yes。即使 bank 放好了，游戏也可能不加载 mod。</p>
             <div class="field-row tight"><button class="btn small" type="button" data-enable-mod="1">启用 enable_mod</button></div>`
      }
      ${
        config.fmod_sound_enable === false
          ? '<p class="hint small">fmod_sound_enable 当前是 no；安装时可勾选一并改成 yes（官方社区步骤通常会写这一行）。</p>'
          : ""
      }
    `;
  }

  function renderInstalled(data) {
    state.installed = data;
    const box = el("pack-installed");
    const count = el("pack-installed-count");
    if (!box) return;
    const packs = data?.packs || [];
    if (count) {
      count.textContent = packs.length ? `${packs.length} 个` : "暂无";
      count.className = `pill ${packs.length ? "subtle" : "subtle"}`.trim();
    }
    const restoreAll = el("pack-restore-all");
    if (restoreAll) {
      restoreAll.hidden = !(data?.valid && packs.length);
      restoreAll.disabled = Boolean(data?.running);
    }

    if (!data || !data.valid) {
      box.innerHTML = `<div class="pack-empty">选择战雷目录后才能查看安装状态。${
        data?.reason ? `<br><span class="small">${escape(data.reason)}</span>` : ""
      }</div>`;
      return;
    }

    const items = packs.map((entry) => {
      const banks = entry.banks || [];
      const staleCount = banks.filter((bank) => bank.stale).length;
      const status = !entry.healthy
        ? { text: "有 bank 被改动", tone: "warn" }
        : staleCount
          ? { text: "游戏已更新", tone: "warn" }
          : { text: "正常", tone: "ok" };
      const rows = banks
        .map((bank) => {
          const info = installedStatus(bank.status);
          if (bank.status === "ok" && bank.stale) {
            info.text = "游戏原版 bank 更新过，建议重装";
            info.tone = "warn";
          }
          return `<tr>
            <td class="mono">${escape(bank.name)}</td>
            <td><span class="pill ${info.tone}">${escape(info.text)}</span></td>
          </tr>`;
        })
        .join("");
      const applied = Object.entries(entry.config_flags || {})
        .filter(([, value]) => value === true)
        .map(([key]) => key);
      return `
        <article class="installed-pack">
          <div class="installed-head">
            <div>
              <div class="installed-title">${escape(entry.name || "未命名语音包")}</div>
              <div class="installed-meta">
                ${escape(sourceLabel(entry.source_type))} · ${banks.length} 个 bank · ${escape(formatTime(entry.installed_at))}
                ${entry.masterbank ? " · 含 masterbank 降噪包" : ""}
                ${applied.length ? ` · 写入 ${escape(applied.join(" / "))}` : ""}
              </div>
            </div>
            <span class="pill ${status.tone}">${escape(status.text)}</span>
          </div>
          ${
            staleCount
              ? '<p class="hint small" style="color:var(--warn)">游戏更新过，这些旧 bank 仍会盖住新版同名文件；建议重新生成语音包或卸载后重装。</p>'
              : ""
          }
          <div class="table-wrap">
            <table class="table">
              <thead><tr><th>Bank</th><th>状态</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
          <div class="installed-actions">
            <button class="btn ghost small" type="button" data-uninstall="${escape(entry.id)}">卸载并还原</button>
          </div>
        </article>`;
    });

    const risky = data.risky || [];
    const riskyHtml = risky.length
      ? `<div class="pack-alert">
           <strong>sound/mod 里有社区已知的高风险 bank</strong>
           <div>${risky
             .map((item) => `<div>· <span class="mono">${escape(item.name)}</span>：${escape(item.reason)}</div>`)
             .join("")}</div>
           <p class="hint small">确认不是你有意安装的，再手动移出 sound/mod；本工具不会自动删除来源不明的文件。</p>
         </div>`
      : "";

    const untracked = data.untracked || [];
    const untrackedHtml = untracked.length
      ? `<details class="details">
           <summary>sound/mod 里还有 ${untracked.length} 个不是本工具安装的 bank（不会自动删除）</summary>
           <div class="chips">${untracked
             .map((item) => `<span class="pill subtle mono">${escape(item.name)}</span>`)
             .join("")}</div>
         </details>`
      : "";

    const backupHtml = packs.length
      ? `<div class="pack-backup-note"><strong>安装前备份已保存：</strong>被覆盖的 bank 和 config.blk 都在本机 <span class="mono">data/packs/backups</span>。卸载或点“还原原版音效”会恢复原文件。</div>`
      : "";
    box.innerHTML = packs.length
      ? backupHtml + riskyHtml + items.join("") + untrackedHtml
      : `<div class="pack-empty">还没有安装记录。把语音包拖到上面解析后安装。</div>${riskyHtml}${untrackedHtml}`;
  }

  function previewBanks(pack, game) {
    const conflicts = new Map((game?.conflicts || []).map((item) => [item.name, item]));
    return (pack?.banks || [])
      .map((bank) => {
        const info = bankStatus(conflicts.get(bank.name));
        return `<tr>
          <td class="mono">${escape(bank.name)}</td>
          <td class="mono">${escape(formatBytes(bank.size))}</td>
          <td><span class="pill ${info.tone}">${escape(info.text)}</span></td>
        </tr>`;
      })
      .join("");
  }

  function ownerConflictHtml() {
    const data = state.conflicts;
    if (!data) return "";
    const rows = data.banks || [];
    if (!rows.length) {
      return `<div class="pack-owner-ok">同名 bank 冲突：没有发现其他已安装包占用这个包里的 bank。</div>`;
    }
    const items = rows
      .map((row) => {
        const owners = (row.owners || [])
          .map((owner) => `${owner.pack || owner.pack_id || "未知包"}${owner.token ? `（${owner.token}）` : ""}`)
          .join("、");
        return `<li><span class="mono">${escape(row.bank)}</span> — ${
          row.action === "same_audio_shared"
            ? "音频与其他包相同，但只会保留一份"
            : `安装会整张覆盖：${escape(owners || "来源不明的包")}`
        }</li>`;
      })
      .join("");
    return `<div class="pack-alert">
      <strong>共用 bank 冲突（不会自动合并）</strong>
      <div>${escape(data.note || "")}</div>
      <ul class="pack-conflict-list">${items}</ul>
    </div>`;
  }

  function contentsHtml() {
    const data = state.contents;
    if (!data) return "";
    const banks = data.banks || [];
    const lines = data.lines || [];
    const bankRows = banks
      .map(
        (bank) => `<tr>
          <td class="mono">${escape(bank.name || "")}</td>
          <td>${bank.event_total || 0} 个事件 / ${bank.stream_total || 0} 条流</td>
          <td>${Number(bank.duration_sec || 0).toFixed(1)} 秒</td>
          <td>${bank.error ? `<span style="color:var(--warn)">${escape(bank.error)}</span>` : "可读"}</td>
        </tr>`,
      )
      .join("");
    const streamDetails = banks
      .filter((bank) => (bank.streams || []).length)
      .map((bank) => {
        const shown = (bank.streams || []).slice(0, 200);
        const rows = shown
          .map(
            (stream) => `<tr><td>${stream.index}</td><td class="mono">${escape(stream.name)}</td><td>${Number(stream.duration_sec || 0).toFixed(2)} 秒</td></tr>`,
          )
          .join("");
        return `<details class="details">
          <summary>${escape(bank.name)} · ${bank.stream_total} 条流${
            bank.stream_total > shown.length ? `（显示前 ${shown.length} 条）` : ""
          }</summary>
          <div class="table-wrap"><table class="table">
            <thead><tr><th>流序号</th><th>流名</th><th>时长</th></tr></thead>
            <tbody>${rows}</tbody>
          </table></div>
        </details>`;
      })
      .join("");
    const lineRows = lines
      .slice(0, 200)
      .map(
        (line) => `<tr><td class="mono">${escape(line.event || "")}</td><td>${escape(line.bank || "")}</td><td>${escape(line.text || "（未填文本）")}</td></tr>`,
      )
      .join("");
    const lineTable = lines.length
      ? `<details class="details">
          <summary>作者写入的台词映射 · ${data.lines_total || lines.length} 条${
            (data.lines_total || 0) > 200 ? "（显示前 200 条）" : ""
          }</summary>
          <div class="table-wrap"><table class="table">
            <thead><tr><th>事件</th><th>bank</th><th>台词</th></tr></thead>
            <tbody>${lineRows}</tbody>
          </table></div>
        </details>`
      : '<p class="hint small">这个包没有台词映射；社区包通常只能在上面看到 bank 内的流名。</p>';
    return `<div class="pack-contents">
      <div class="pack-contents-head">
        <strong>包内事件（只读）</strong>
        <span class="hint small">${escape(data.note || "")}</span>
      </div>
      <div class="table-wrap"><table class="table">
        <thead><tr><th>Bank</th><th>事件 / 流</th><th>总时长</th><th>解析</th></tr></thead>
        <tbody>${bankRows}</tbody>
      </table></div>
      ${lineTable}
      ${streamDetails}
    </div>`;
  }

  function renderPreview() {
    const box = el("pack-preview");
    const pack = state.pack;
    const game = state.game;
    if (!box) return;
    if (!pack) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;

    const manifest = pack.manifest || null;
    const lines = Number(manifest?.lines || manifest?.mapping?.length || 0);
    const warnings = pack.warnings || [];
    const invalid = !game?.valid;
    const running = Boolean(game?.running);
    const conflictList = (game?.conflicts || []).filter((item) => item.mod_present && !item.mod_same);
    const config = configState(game);
    const required = manifest?.config && typeof manifest.config === "object" ? manifest.config : {};
    const protectedBanks = (pack.banks || []).filter((bank) => bank.protected);
    const hasProtected = protectedBanks.length > 0;
    const alreadyInstalled = Boolean(
      (state.installed?.packs || []).some((item) => item.token === pack.token),
    );
    const crewVoice = (pack.banks || []).some((bank) =>
      String(bank.name || "").toLowerCase().includes("crew_dialogs"),
    );
    const canInstall =
      !invalid && !running && !state.installing && (!hasProtected || state.allowMasterbank);

    const flagBox = (id, title, desc, current, fallback) => {
      const on = current === true || (current !== true && fallback);
      return `<label class="pack-check ${current === true ? "disabled" : ""}">
        <input type="checkbox" id="${id}" ${on ? "checked" : ""} ${current === true ? "disabled" : ""}>
        <span>${title}${desc ? ` <span class="muted small">${desc}</span>` : ""}</span>
      </label>`;
    };

    const sample = Array.isArray(manifest?.mapping) ? manifest.mapping.slice(0, 6) : [];
    const sampleHtml = sample.length
      ? `<details class="details">
           <summary>台词样例（前 ${sample.length} 条，共 ${lines} 条）</summary>
           <div class="table-wrap">
             <table class="table">
               <thead><tr><th>事件</th><th>台词</th></tr></thead>
               <tbody>${sample
                 .map(
                   (row) => `<tr>
                     <td class="mono">${escape(row.event || row.event_id || "")}</td>
                     <td>${escape(row.text || "（未填文本）")}</td>
                   </tr>`,
                 )
                 .join("")}</tbody>
             </table>
           </div>
         </details>`
      : "";

    const hint = invalid
      ? `<span style="color:var(--warn)">${escape(game?.reason || "还没有选择战雷目录。")}</span>`
      : running
        ? '<span style="color:var(--warn)">游戏正在运行，请先关闭游戏。</span>'
        : `将写入 <span class="mono">${escape(game.mod_dir || "sound/mod")}</span>。`;

    box.innerHTML = `
      <div class="pack-file">
        <div>
          <div class="pack-file-name">${escape(pack.original_name || pack.name)}</div>
          <div class="muted small">
            ${escape(pack.source_format ? `${String(pack.source_format).toUpperCase()} 语音包` : sourceLabel(pack.source_type))} · ${pack.bank_count} 个 bank · ${escape(formatBytes(pack.size))}
            ${layoutLabel(pack.layout) ? ` · ${escape(layoutLabel(pack.layout))}` : ""}
            ${lines ? ` · ${lines} 条台词` : ""}
            ${pack.extra_files ? ` · 忽略 ${pack.extra_files} 个非 bank 文件` : ""}
          </div>
        </div>
        <span class="pill ok">已解析</span>
      </div>
      ${
        warnings.length
          ? `<div class="pack-warnings">${warnings.map((item) => `<div>· ${escape(item)}</div>`).join("")}</div>`
          : ""
      }
      ${ownerConflictHtml()}
      <div class="pack-contents-actions">
        <button class="btn ghost small" id="pack-contents-btn" type="button" ${state.contentsBusy ? "disabled" : ""}>
          ${state.contentsBusy ? "正在读取包内事件…" : state.contents ? "重新读取包内事件" : "查看包内全部事件"}
        </button>
        <span class="hint small">按 bank 查看流名、时长；有 manifest 时显示作者写入的台词。</span>
      </div>
      ${contentsHtml()}
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>Bank</th><th>体积</th><th>安装动作</th></tr></thead>
          <tbody>${previewBanks(pack, game)}</tbody>
        </table>
      </div>
      ${sampleHtml}
      ${
        hasProtected
          ? `<div class="pack-alert">
               <strong>${pack.wave_like ? "疑似 W.A.V.E. 风格降噪包" : "包含 masterbank 核心 bank"}</strong>
               <div>${protectedBanks
                 .map((bank) => `<span class="mono">${escape(bank.name)}</span>`)
                 .join("、")}</div>
               <div>masterbank 控制全局混音；装错或旧版 W.A.V.E. 可能导致枪炮/引擎声消失、卡顿甚至崩溃。建议使用 W.A.V.E. 4.0。</div>
               <label class="pack-check">
                 <input type="checkbox" id="pack-allow-masterbank" ${state.allowMasterbank ? "checked" : ""}>
                 <span>我确认这是降噪/兼容包，允许安装 masterbank（安装前会备份 mod 里现有文件，卸载可还原）</span>
               </label>
             </div>`
          : ""
      }
      ${flagBox(
        "pack-enable-mod",
        "enable_mod:b=yes",
        "官方必需；不勾选只放 bank，不使用 config 修改",
        config.enable_mod,
        true,
      )}
      ${flagBox(
        "pack-fmod",
        "fmod_sound_enable:b=yes",
        "社区常用；已经是 yes 时自动跳过",
        config.fmod_sound_enable,
        true,
      )}
      <details class="details pack-advanced">
        <summary>高级：枪炮音效作者的额外开关</summary>
        ${flagBox(
          "pack-loops-gun",
          "useLoopsForGunSound:b=yes",
          "快速射击的飞机机炮按循环方式播放",
          config.use_loops_gun_sound,
          required.use_loops_gun_sound === true,
        )}
        ${flagBox(
          "pack-loops-gm-gun",
          "useLoopsForGMGunSound:b=yes",
          "地面/舰船快速射击炮的循环模式",
          config.use_loops_gm_gun_sound,
          required.use_loops_gm_gun_sound === true,
        )}
        <p class="hint small">只有包作者明确要求时再勾选；不确定就保持默认。所有改动都会先备份 config.blk。</p>
      </details>
      ${
        crewVoice
          ? '<p class="hint small">这是乘员语音包：安装后到 设置 → 声音 → 成员组语言，按作者说明选“游戏选项”或“成员组国籍”。</p>'
          : ""
      }
      <div class="pack-backup-note"><strong>安装前自动备份：</strong>${
        conflictList.length
          ? `会先备份被覆盖的 ${conflictList.length} 个 bank，`
          : ""
      }同时备份 config.blk。卸载或“还原原版音效”时可以恢复。</div>
      <p class="hint small">${hint}${alreadyInstalled ? " 这个包已经安装，可以重新安装或到下方卸载。" : ""}</p>
      <div class="pack-actions">
        <button class="btn" id="pack-install-btn" type="button" ${canInstall ? "" : "disabled"}>
          ${state.installing
            ? "正在安装…"
            : hasProtected && !state.allowMasterbank
              ? "先允许 masterbank"
              : alreadyInstalled
                ? "重新安装"
                : conflictList.length
                  ? "覆盖并安装"
                  : "安装到游戏"}
        </button>
        <button class="btn ghost" id="pack-discard-btn" type="button" ${state.installing || alreadyInstalled ? "disabled" : ""}>放弃这份解析</button>
        <span class="spacer"></span>
        <span class="hint small">覆盖前旧 bank 会备份到本地数据目录</span>
      </div>
    `;
  }

  async function loadInstalled({ silent = false } = {}) {
    try {
      const data = await apiCall("/api/packs/installed");
      renderInstalled(data);
      renderGame(data);
      showError("");
      return data;
    } catch (error) {
      if (!silent) showError(error.message);
      throw error;
    }
  }

  async function inspectPack() {
    if (!state.pack) return;
    try {
      const data = await apiCall(`/api/packs/inspect?token=${encodeURIComponent(state.pack.token)}`);
      state.pack = data.pack;
      renderGame(data.game);
      renderPreview();
      loadPackConflicts();
    } catch (error) {
      showError(error.message);
    }
  }

  async function loadPackConflicts() {
    if (!state.pack) return;
    try {
      const data = await apiCall(
        `/api/packs/conflicts?token=${encodeURIComponent(state.pack.token)}`,
      );
      state.conflicts = data;
      renderPreview();
    } catch {
      state.conflicts = null;
    }
  }

  async function loadPackContents() {
    if (!state.pack || state.contentsBusy) return;
    state.contentsBusy = true;
    renderPreview();
    try {
      const data = await apiCall(
        `/api/packs/contents?token=${encodeURIComponent(state.pack.token)}`,
      );
      state.contents = data;
      toastMessage(
        `已读取 ${data.bank_count} 个 bank · ${data.lines_total || 0} 条台词映射`,
      );
    } catch (error) {
      showError(error.message);
      toastMessage(error.message, "bad");
    } finally {
      state.contentsBusy = false;
      renderPreview();
    }
  }

  function uploadFile(file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const url = `/api/packs/upload?filename=${encodeURIComponent(file.name)}`;
      xhr.open("POST", url);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.timeout = 10 * 60 * 1000;
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        const percent = Math.min(99, Math.round((event.loaded / event.total) * 100));
        el("pack-upload-fill").style.width = `${percent}%`;
        el("pack-upload-text").textContent =
          `正在上传 ${percent}% · ${formatBytes(event.loaded)} / ${formatBytes(event.total)}`;
      };
      xhr.onload = () => {
        let payload = {};
        try {
          payload = JSON.parse(xhr.responseText || "{}");
        } catch {
          payload = {};
        }
        if (xhr.status >= 200 && xhr.status < 300 && payload.ok) resolve(payload);
        else reject(new Error(payload.error || `上传失败（${xhr.status}）`));
      };
      xhr.onerror = () => reject(new Error("上传失败，请确认本地服务仍在运行"));
      xhr.ontimeout = () => reject(new Error("上传超时，请重试"));
      el("pack-upload-fill").style.width = "0%";
      el("pack-upload-text").textContent = `准备上传 ${formatBytes(file.size)}…`;
      xhr.send(file);
    });
  }

  async function selectFile(file) {
    if (!file || state.uploading) return;
    showError("");
    const name = String(file.name || "").toLowerCase();
    if (!ALLOWED.some((suffix) => name.endsWith(suffix))) {
      showError("只支持 .wtvoice、.zip、.rar、.7z 或 .bank 文件。");
      toastMessage("文件类型不支持", "bad");
      return;
    }
    if (file.size > MAX_BYTES) {
      showError("语音包超过 1 GB，已拒绝。");
      toastMessage("语音包过大", "bad");
      return;
    }

    state.uploading = true;
    state.pack = null;
    state.contents = null;
    state.conflicts = null;
    state.allowMasterbank = false;
    renderPreview();
    el("pack-upload").hidden = false;
    el("page-packs")?.classList.add("pack-uploading");
    try {
      const payload = await uploadFile(file);
      state.pack = payload.pack;
      state.game = payload.game;
      state.contents = null;
      state.conflicts = null;
      renderGame(payload.game);
      renderPreview();
      loadPackConflicts();
      toastMessage(`已解析：${payload.pack.name}`);
    } catch (error) {
      showError(error.message);
      toastMessage(error.message, "bad");
    } finally {
      state.uploading = false;
      el("page-packs")?.classList.remove("pack-uploading");
      el("pack-upload").hidden = true;
      el("pack-file").value = "";
    }
  }

  async function installPack() {
    if (!state.pack || state.installing) return;
    if (!state.game?.valid) {
      showError(state.game?.reason || "请先选择有效的战雷目录。");
      return;
    }
    if (state.game?.running) {
      showError("游戏正在运行，请先关闭游戏再安装。");
      return;
    }
    const overwritten = (state.game.conflicts || []).filter(
      (item) => item.mod_present && !item.mod_same,
    );
    if (overwritten.length) {
      const owners = [...new Set(overwritten.map((item) => item.owner_name).filter(Boolean))];
      const external = overwritten.some((item) => !item.owner_name);
      const ownerText = owners.length
        ? `同名 bank 属于：${owners.slice(0, 3).join("、")}${owners.length > 3 ? " 等" : ""}。`
        : "这些 bank 不在本工具的安装记录里。";
      if (
        !window.confirm(
          `sound/mod 里有 ${overwritten.length} 个同名 bank 会被覆盖。${ownerText}` +
            (external ? "其中包含来源不明的文件。" : "") +
            "旧文件和 config.blk 会先备份，之后可以还原。继续安装？",
        )
      ) {
        return;
      }
    }
    const config = {};
    [
      ["pack-enable-mod", "enable_mod"],
      ["pack-fmod", "fmod_sound_enable"],
      ["pack-loops-gun", "use_loops_gun_sound"],
      ["pack-loops-gm-gun", "use_loops_gm_gun_sound"],
    ].forEach(([id, key]) => {
      const node = el(id);
      if (node && !node.disabled && node.checked) config[key] = true;
    });

    state.installing = true;
    renderPreview();
    try {
      const payload = await apiCall("/api/packs/install", {
        method: "POST",
        body: JSON.stringify({
          token: state.pack.token,
          config,
          allow_masterbank: state.allowMasterbank,
        }),
      });
      renderInstalled(payload.installed);
      renderGame(payload.installed);
      toastMessage(`已安装：${payload.pack.name} · 启动后看左上角是否显示“已启用自定义音效”`);
      await inspectPack();
    } catch (error) {
      showError(error.message);
      toastMessage(error.message, "bad");
    } finally {
      state.installing = false;
      renderPreview();
    }
  }

  async function uninstallPack(packId) {
    const entry = (state.installed?.packs || []).find((item) => item.id === packId);
    if (!entry) return;
    if (!window.confirm(`卸载“${entry.name}”？只会删除本工具安装且未被改动的 bank，并尽量还原旧文件。`)) {
      return;
    }
    try {
      const payload = await apiCall("/api/packs/uninstall", {
        method: "POST",
        body: JSON.stringify({ pack_id: packId }),
      });
      renderInstalled(payload.installed);
      renderGame(payload.installed);
      const parts = [];
      if (payload.removed) parts.push(`移除 ${payload.removed} 个 bank`);
      if (payload.restored) parts.push(`还原 ${payload.restored} 个备份`);
      if (payload.skipped) parts.push(`${payload.skipped} 个被改动已跳过`);
      toastMessage(parts.length ? `已卸载：${parts.join("，")}` : "已删除安装记录");
      await inspectPack();
    } catch (error) {
      showError(error.message);
      toastMessage(error.message, "bad");
    }
  }

  async function restoreAllPacks() {
    const packs = state.installed?.packs || [];
    if (!packs.length) return;
    if (
      !window.confirm(
        `还原原版音效会卸载 ${packs.length} 个语音包，并尽量恢复安装前的 bank 和 config.blk。\n\n` +
          "不会删除语音包文件本身；之后再安装即可恢复。继续？",
      )
    ) {
      return;
    }
    try {
      const payload = await apiCall("/api/packs/restore-all", {
        method: "POST",
        body: "{}",
      });
      renderInstalled(payload.installed);
      renderGame(payload.installed);
      const parts = [`卸载 ${payload.removed_packs || 0} 个语音包`];
      if (payload.restored) parts.push(`还原 ${payload.restored} 个 bank`);
      if (payload.config_restored) parts.push("config.blk 已还原");
      if (payload.remaining_packs) parts.push(`仍有 ${payload.remaining_packs} 个未卸载`);
      if (payload.errors?.length) parts.push(`${payload.errors.length} 个失败`);
      toastMessage(parts.join("，"), payload.errors?.length ? "bad" : "ok");
      if (payload.errors?.length) {
        showError(
          payload.errors
            .map((item) => `${item.name}：${item.error}`)
            .join("；"),
        );
      } else {
        showError("");
      }
      if (payload.untracked?.length) {
        toastMessage(
          `sound/mod 里还留有 ${payload.untracked.length} 个非本工具 bank，未自动删除`,
          "bad",
        );
      }
      await inspectPack();
    } catch (error) {
      showError(error.message);
      toastMessage(error.message, "bad");
    }
  }

  async function discardPack() {
    if (!state.pack) return;
    try {
      await apiCall("/api/packs/discard", {
        method: "POST",
        body: JSON.stringify({ token: state.pack.token }),
      });
      state.pack = null;
      state.allowMasterbank = false;
      renderPreview();
      toastMessage("已放弃这份解析");
    } catch (error) {
      showError(error.message);
      toastMessage(error.message, "bad");
    }
  }

  async function enableMod() {
    try {
      await apiCall("/api/packs/enable-mod", {
        method: "POST",
        body: JSON.stringify({ enabled: true }),
      });
      toastMessage("已开启 enable_mod");
      await loadInstalled();
      await inspectPack();
    } catch (error) {
      showError(error.message);
      toastMessage(error.message, "bad");
    }
  }

  function bindEvents() {
    const drop = el("pack-drop");
    const input = el("pack-file");
    if (!drop || !input) return;

    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
    input.addEventListener("change", () => selectFile(input.files?.[0]));

    let dragDepth = 0;
    const setDrag = (active) => drop.classList.toggle("dragging", active);
    drop.addEventListener("dragenter", (event) => {
      event.preventDefault();
      dragDepth += 1;
      setDrag(true);
    });
    drop.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    drop.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) setDrag(false);
    });
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      dragDepth = 0;
      setDrag(false);
      selectFile(event.dataTransfer?.files?.[0]);
    });

    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());

    el("packs-refresh-btn")?.addEventListener("click", async () => {
      try {
        await loadInstalled();
        await inspectPack();
        toastMessage("已刷新安装状态");
      } catch (error) {
        showError(error.message);
      }
    });

    el("pack-preview")?.addEventListener("click", (event) => {
      if (event.target.closest("#pack-install-btn")) installPack();
      else if (event.target.closest("#pack-discard-btn")) discardPack();
      else if (event.target.closest("#pack-contents-btn")) loadPackContents();
    });
    el("pack-preview")?.addEventListener("change", (event) => {
      if (event.target.id === "pack-allow-masterbank") {
        state.allowMasterbank = event.target.checked;
        renderPreview();
      }
    });

    el("pack-installed")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-uninstall]");
      if (button) uninstallPack(button.dataset.uninstall);
    });
    el("pack-restore-all")?.addEventListener("click", restoreAllPacks);

    el("pack-game-info")?.addEventListener("click", (event) => {
      if (event.target.closest("[data-enable-mod]")) enableMod();
    });

    window.addEventListener("hashchange", () => {
      if (location.hash !== "#packs") return;
      loadInstalled({ silent: true })
        .then(() => inspectPack())
        .catch(() => {});
    });
  }

  async function init() {
    if (state.initialized || !el("page-packs")) return;
    state.initialized = true;
    bindEvents();
    try {
      await loadInstalled({ silent: true });
      await inspectPack();
    } catch (error) {
      showError(error.message);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
