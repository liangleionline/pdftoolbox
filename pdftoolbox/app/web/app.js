// PDF 转换工具箱 前端逻辑
const PAGE_META = {
  "img-to-pdf":     { title: "图片转 PDF",     desc: "将多张 JPG / PNG / WebP 图片合并为一个 PDF 文件" },
  "pdf-to-img":     { title: "PDF 转图片",     desc: "将 PDF 每一页导出为 PNG / JPEG，打包为 ZIP" },
  "word-to-pdf":    { title: "Word 转 PDF",    desc: "将 .doc / .docx 文档转换为 PDF" },
  "excel-to-pdf":   { title: "Excel 转 PDF",   desc: "将 .xls / .xlsx 表格转换为 PDF" },
  "ppt-to-pdf":     { title: "PPT 转 PDF",     desc: "将 .ppt / .pptx 演示文稿转换为 PDF" },
  "txt-to-pdf":     { title: "TXT 转 PDF",     desc: "将纯文本 .txt 文件转换为 PDF" },
  "pdf-merge":      { title: "PDF 整合",       desc: "将多个 PDF 按顺序整合为一个 PDF" },
  "ebook-to-pdf":   { title: "电子书转 PDF",   desc: "将 EPUB / MOBI / TXT 等电子书转换为 PDF" },
  "archive-to-pdf": { title: "压缩包转 PDF",   desc: "将 ZIP / RAR / 7Z 压缩包中的图片或文档转换为 PDF" },
  "deps":           { title: "外部依赖",       desc: "查看本应用所需的外部命令行工具状态" },
};

const state = { files: {} };  // panel -> File[]

// ---------- 菜单切换 + 移动端抽屉 ----------
document.querySelectorAll(".menu-item").forEach(item => {
  item.addEventListener("click", e => {
    e.preventDefault();
    showPanel(item.dataset.panel);
    closeSidebar();
  });
});

const menuToggle = document.getElementById("menu-toggle");
const sidebarEl = document.querySelector(".sidebar");
const overlayEl = document.getElementById("sidebar-overlay");
function openSidebar() { sidebarEl.classList.add("open"); overlayEl.classList.add("show"); }
function closeSidebar() { sidebarEl.classList.remove("open"); overlayEl.classList.remove("show"); }
if (menuToggle) {
  menuToggle.addEventListener("click", () => {
    if (sidebarEl.classList.contains("open")) closeSidebar(); else openSidebar();
  });
  overlayEl.addEventListener("click", closeSidebar);
}

function showPanel(name) {
  document.querySelectorAll(".menu-item").forEach(i =>
    i.classList.toggle("active", i.dataset.panel === name));
  document.querySelectorAll(".panel").forEach(p =>
    p.hidden = p.id !== "panel-" + name);
  const meta = PAGE_META[name];
  if (meta) {
    document.getElementById("page-title").textContent = meta.title;
    document.getElementById("page-desc").textContent = meta.desc;
  }
}

// ---------- 弹窗组件 ----------
let modalConfirmCb = null;
function showModal(title, options, confirmCb) {
  document.getElementById("modal-title").textContent = title;
  const body = document.getElementById("modal-body");
  body.innerHTML = options.map((o, i) =>
    `<button class="modal-option" data-val="${escapeHtml(o.value)}">
       ${escapeHtml(o.label)}
       ${o.desc ? `<span class="opt-desc">${escapeHtml(o.desc)}</span>` : ""}
     </button>`).join("");
  let selected = null;
  body.querySelectorAll(".modal-option").forEach(btn => {
    btn.addEventListener("click", () => {
      body.querySelectorAll(".modal-option").forEach(b => b.classList.remove("selected"));
      btn.classList.add("selected");
      selected = btn.dataset.val;
    });
  });
  modalConfirmCb = () => {
    if (!selected) { return; }
    hideModal();
    confirmCb(selected);
  };
  document.getElementById("modal-overlay").hidden = false;
}
function hideModal() {
  document.getElementById("modal-overlay").hidden = true;
  modalConfirmCb = null;
}
document.getElementById("modal-cancel").addEventListener("click", hideModal);
document.getElementById("modal-confirm").addEventListener("click", () => {
  if (modalConfirmCb) modalConfirmCb();
});

// ---------- 初始化各面板 ----------
const PANELS = [
  { key: "img-to-pdf",   multiple: true,  url: "/api/convert/images-to-pdf" },
  { key: "pdf-to-img",   multiple: false, url: "/api/convert/pdf-to-images" },
  { key: "word-to-pdf",  multiple: false, url: "/api/convert/office-to-pdf" },
  { key: "excel-to-pdf", multiple: false, url: "/api/convert/office-to-pdf" },
  { key: "ppt-to-pdf",   multiple: false, url: "/api/convert/office-to-pdf" },
  { key: "txt-to-pdf",   multiple: false, url: "/api/convert/office-to-pdf" },
  { key: "pdf-merge",    multiple: true,  url: "/api/merge/pdf" },
  { key: "ebook-to-pdf", multiple: false, url: "/api/convert/ebook-to-pdf" },
  { key: "archive-to-pdf", multiple: false, url: null },  // 压缩包走特殊流程
];

const SORTABLE_KEYS = new Set(["img-to-pdf", "pdf-merge"]);

PANELS.forEach(p => {
  const dz = document.getElementById("dz-" + p.key);
  const input = document.getElementById("input-" + p.key);
  if (!dz) return;
  dz.addEventListener("click", () => input.click());
  input.addEventListener("change", () => setFiles(p.key, input.files));
  dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("dragover"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
  dz.addEventListener("drop", e => {
    e.preventDefault();
    dz.classList.remove("dragover");
    setFiles(p.key, e.dataTransfer.files);
  });
});

function setFiles(key, fileList) {
  const p = PANELS.find(x => x.key === key);
  const arr = Array.from(fileList);
  if (p.multiple) {
    const existing = state.files[key] || [];
    const seen = new Set(existing.map(f => f.name + ":" + f.size));
    for (const f of arr) {
      if (!seen.has(f.name + ":" + f.size)) existing.push(f);
    }
    state.files[key] = existing;
  } else {
    state.files[key] = arr.slice(0, 1);
  }
  renderFileList(key);
}

function renderFileList(key) {
  const list = document.getElementById("list-" + key);
  const files = state.files[key] || [];
  if (!list) return;

  if (SORTABLE_KEYS.has(key)) {
    list.innerHTML = files.map((f, i) =>
      `<div class="file-item sortable-item" draggable="true" data-idx="${i}">
         <span class="grip">⠿</span>
         <span class="step">${i + 1}.</span>
         <span class="fname" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
         <span class="size">${humanSize(f.size)}</span>
         <button class="remove" data-idx="${i}" title="移除">✕</button>
       </div>`).join("");
    list.querySelectorAll(".remove").forEach(btn => {
      btn.addEventListener("click", e => {
        e.stopPropagation();
        state.files[key].splice(parseInt(btn.dataset.idx, 10), 1);
        renderFileList(key);
      });
    });
    bindSortable(list, key);
    const hint = document.getElementById("hint-" + key);
    if (hint) hint.textContent = files.length ? `共 ${files.length} 个，将按当前顺序处理` : "";
  } else {
    list.innerHTML = files.map(f =>
      `<div class="file-item"><span>${escapeHtml(f.name)}</span><span class="size">${humanSize(f.size)}</span></div>`).join("");
  }
}

function bindSortable(listEl, key) {
  let dragIdx = null;
  listEl.querySelectorAll(".sortable-item").forEach(item => {
    item.addEventListener("dragstart", e => {
      dragIdx = parseInt(item.dataset.idx, 10);
      item.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    item.addEventListener("dragend", () => {
      item.classList.remove("dragging");
      listEl.querySelectorAll(".sortable-item").forEach(x => x.classList.remove("drag-over"));
      dragIdx = null;
    });
    item.addEventListener("dragover", e => { e.preventDefault(); item.classList.add("drag-over"); });
    item.addEventListener("dragleave", () => item.classList.remove("drag-over"));
    item.addEventListener("drop", e => {
      e.preventDefault();
      item.classList.remove("drag-over");
      const dropIdx = parseInt(item.dataset.idx, 10);
      if (dragIdx === null || dragIdx === dropIdx) return;
      const arr = state.files[key];
      const [moved] = arr.splice(dragIdx, 1);
      arr.splice(dropIdx, 0, moved);
      renderFileList(key);
    });
  });
}

function humanSize(n) {
  const u = ["B","KB","MB","GB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + " " + u[i];
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

// 把任意 HTTP 响应转成可读错误文本（兼容服务器返回 JSON 或意外 HTML 的情况）
async function errorText(resp) {
  let text = "";
  try { text = await resp.text(); } catch (e) { text = ""; }
  text = (text || "").trim();
  if (text.startsWith("<")) {
    return `服务器返回了异常页面（HTTP ${resp.status}）。若本应用内"外部依赖"均正常仍出现，请查看应用日志或确认已更新到最新版本。`;
  }
  try { const j = JSON.parse(text); return j.error || ("HTTP " + resp.status); }
  catch (e) { return text ? text.slice(0, 200) : ("HTTP " + resp.status); }
}

// ---------- 排序 / 清空 ----------
function bindQueueButtons(key, sortBtnId, clearBtnId) {
  document.getElementById(sortBtnId).addEventListener("click", () => {
    const arr = state.files[key] || [];
    arr.sort((a, b) => a.name.localeCompare(b.name, "zh-CN", { numeric: true }));
    renderFileList(key);
  });
  document.getElementById(clearBtnId).addEventListener("click", () => {
    state.files[key] = [];
    const input = document.getElementById("input-" + key);
    if (input) input.value = "";
    renderFileList(key);
  });
}
bindQueueButtons("img-to-pdf", "btn-sort-name", "btn-clear-list");
bindQueueButtons("pdf-merge", "btn-merge-sort-name", "btn-merge-clear");

// ---------- 转换 ----------
PANELS.forEach(p => {
  if (p.key === "archive-to-pdf") return;
  const btn = document.getElementById("btn-" + p.key);
  if (!btn) return;
  btn.addEventListener("click", () => convert(p));
});

async function convert(p) {
  const files = state.files[p.key] || [];
  if (!files.length) { setStatus(p.key, "请先选择文件", "error"); return; }
  const btn = document.getElementById("btn-" + p.key);
  btn.disabled = true;
  setStatus(p.key, "已提交，处理中…");
  try {
    const fd = new FormData();
    files.forEach(f => fd.append("files", f, f.name));
    let url = p.url;
    if (p.key === "pdf-to-img") {
      const fmt = document.getElementById("opt-img-fmt").value;
      const dpi = document.getElementById("opt-img-dpi").value;
      url += `?fmt=${fmt}&dpi=${dpi}`;
    }
    const resp = await fetch(url, { method: "POST", body: fd });
    const j = await resp.json();
    if (!j.ok) { setStatus(p.key, "失败：" + (j.error || "未知错误"), "error"); return; }
    if (j.task_id) { await pollTask(p.key, j.task_id); }
    else if (j.path) { showDownload(p.key, j); setStatus(p.key, "转换完成", "success"); }
  } catch (e) {
    setStatus(p.key, "请求失败：" + e.message, "error");
  } finally {
    btn.disabled = false;
  }
}

// 异步任务轮询：转换不再长时间占用连接（避免弱机被网关掐断成 Failed to fetch），
// 后台完成后通过 /api/task/<id> 取回结果。
async function pollTask(key, taskId) {
  let tick = 0;
  const spin = ["▖", "▘", "▝", "▗"];
  while (true) {
    await new Promise(r => setTimeout(r, 1200));
    let resp, j;
    try {
      resp = await fetch("/api/task/" + taskId);
      j = await resp.json();
    } catch (e) {
      setStatus(key, "查询进度失败：" + e.message, "error");
      return;
    }
    if (!j.ok) { setStatus(key, "失败：" + (j.error || "任务不存在"), "error"); return; }
    if (j.status === "done") {
      showDownload(key, j);
      setStatus(key, "转换完成", "success");
      return;
    }
    if (j.status === "error") {
      setStatus(key, "失败：" + (j.error || "转换失败"), "error");
      return;
    }
    tick++;
    setStatus(key, "处理中 " + spin[tick % 4]);
  }
}

function downloadBlob(blob, resp) {
  const dispo = resp.headers.get("Content-Disposition") || "";
  let fname = "output";
  const m = dispo.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/);
  if (m) fname = decodeURIComponent(m[1] || m[2] || "output");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function setStatus(key, text, cls) {
  const el = document.getElementById("status-" + key);
  if (!el) return;
  el.textContent = text;
  el.className = "status" + (cls ? " " + cls : "");
}

// 转换成功后展示结果卡片：仅显示保存目录（不再显示/复制下载链接，避免地址解析歧义）
// 适配飞牛 App 的 WebView 无法用 Blob 触发下载的场景。
function showDownload(key, j) {
  const panel = document.getElementById("panel-" + key);
  if (!panel) return;
  panel.querySelectorAll(".download-card").forEach(x => x.remove());
  const card = document.createElement("div");
  card.className = "download-card";
  card.innerHTML =
    '<div class="dc-title">✅ 转换完成</div>' +
    '<div class="dc-file">' + escapeHtml(j.filename || "output") + '</div>' +
    '<div class="dc-path">已保存到目录：<code>' + escapeHtml(j.path || "") + '</code></div>' +
    '<div class="dc-hint">文件已写入上面的目录。可在「设置」中自定义输出目录。</div>';
  const actions = panel.querySelector(".actions");
  if (actions && actions.nextSibling) panel.insertBefore(card, actions.nextSibling);
  else panel.appendChild(card);
}

function copyText(text, btn) {
  const done = () => {
    const old = btn.textContent;
    btn.textContent = "已复制";
    setTimeout(() => { btn.textContent = old; }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(done);
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    done();
  }
}

// ---------- 压缩包转 PDF ----------
document.getElementById("btn-archive-to-pdf").addEventListener("click", async () => {
  const key = "archive-to-pdf";
  const files = state.files[key] || [];
  if (!files.length) { setStatus(key, "请先选择压缩包", "error"); return; }
  const file = files[0];
  const btn = document.getElementById("btn-" + key);
  btn.disabled = true;
  setStatus(key, "正在分析压缩包…");

  // 先分析
  const fd = new FormData();
  fd.append("files", file, file.name);
  let cls;
  try {
    const r = await fetch("/api/archive/analyze", { method: "POST", body: fd });
    const ct = r.headers.get("Content-Type") || "";
    if (!r.ok || !ct.includes("json")) {
      setStatus(key, "分析失败：" + await errorText(r), "error");
      btn.disabled = false;
      return;
    }
    cls = await r.json();
  } catch (e) {
    setStatus(key, "请求失败：" + e.message, "error");
    btn.disabled = false;
    return;
  }
  if (!cls.ok) { setStatus(key, cls.error || "分析失败", "error"); btn.disabled = false; return; }

  const type = cls.type;
  if (type === "mixed") {
    setStatus(key, cls.error || "压缩包混合了图片与文档，请先整理为同一类型", "error");
    btn.disabled = false;
    return;
  }
  if (type === "unsupported" || type === "empty") {
    setStatus(key, cls.error || "压缩包内没有可转换的文件", "error");
    btn.disabled = false;
    return;
  }

  const kindName = type === "image" ? "图片" : "文档";
  const doConvert = mode => convertArchive(file, mode, key, btn);
  if (cls.count > 1) {
    showModal(
      `压缩包内有 ${cls.count} 个${kindName}，如何转换？`,
      [
        { value: "merge", label: "合并为一个 PDF", desc: "所有文件按文件名顺序整合到一个 PDF" },
        { value: "separate", label: "每个文件单独 PDF", desc: "为每个文件分别生成 PDF，打包为 ZIP" },
      ],
      doConvert
    );
  } else {
    doConvert("merge");
  }
});

async function convertArchive(file, mode, key, btn) {
  setStatus(key, mode === "merge" ? "已提交，处理中…" : "已提交，处理中…");
  try {
    const fd = new FormData();
    fd.append("files", file, file.name);
    const resp = await fetch(`/api/archive/convert?mode=${mode}`, { method: "POST", body: fd });
    const j = await resp.json();
    if (!j.ok) {
      setStatus(key, "转换失败：" + (j.error || "未知错误"), "error");
      return;
    }
    if (j.task_id) { await pollTask(key, j.task_id); }
    else if (j.path) { showDownload(key, j); setStatus(key, "转换完成", "success"); }
  } catch (e) {
    setStatus(key, "请求失败：" + e.message, "error");
  } finally {
    btn.disabled = false;
  }
}

// ---------- 工具状态 ----------
let cachedTools = [];
function refreshDeps() {
  return fetch("/api/tools").then(async r => {
    const ct = r.headers.get("Content-Type") || "";
    if (!r.ok || !ct.includes("json")) {
      const txt = await r.text();
      throw new Error(txt.startsWith("<") ? `服务器返回异常（HTTP ${r.status}）` : txt.slice(0, 200));
    }
    const data = await r.json();
    cachedTools = data.tools || [];
    renderDepsBadge();
    renderDepsList();
  });
}
function renderDepsBadge() {
  const missing = cachedTools.filter(t => !t.available && t.install_cmd);
  const badge = document.getElementById("deps-badge");
  if (!missing.length) {
    badge.textContent = "✓ 依赖正常";
    badge.classList.remove("warn"); badge.classList.add("ok");
  } else {
    badge.textContent = `⚠ 依赖异常（${missing.length} 项）`;
    badge.classList.add("warn"); badge.classList.remove("ok");
  }
}
function renderDepsList() {
  const list = document.getElementById("deps-list");
  list.innerHTML = cachedTools.map(t => {
    const status = t.available
      ? '<span class="ok">✓ 已安装</span>'
      : (t.install_cmd
          ? `<span class="no">✗ 未安装</span> <a href="#" class="copy-cmd" data-cmd="${escapeHtml(t.install_cmd)}">复制代码</a>`
          : '<span class="no">✗ 未安装</span>');
    const cmdHtml = t.install_cmd ? `<div class="cmd-line"><code>${escapeHtml(t.install_cmd)}</code></div>` : "";
    return `<div class="dep-row">
      <div class="dep-name">${escapeHtml(t.name)}</div>
      <div class="dep-status">${status}</div>
      ${cmdHtml}
    </div>`;
  }).join("");
  list.querySelectorAll(".copy-cmd").forEach(a => {
    a.addEventListener("click", e => {
      e.preventDefault();
      const cmd = a.dataset.cmd;
      const done = () => {
        const old = a.textContent;
        a.textContent = "已复制";
        setTimeout(() => { a.textContent = old; }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(cmd).then(done).catch(done);
      } else {
        const ta = document.createElement("textarea");
        ta.value = cmd;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        done();
      }
    });
  });
}
document.getElementById("deps-badge").addEventListener("click", () => showPanel("deps"));
document.getElementById("btn-refresh-deps").addEventListener("click", () => refreshDeps().catch(() => {}));
refreshDeps().catch(() => {
  document.getElementById("deps-badge").textContent = "依赖检测失败";
});

// 默认显示第一个面板
showPanel("img-to-pdf");
