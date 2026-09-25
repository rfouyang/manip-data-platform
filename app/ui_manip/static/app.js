// ---- 通用：转义、请求、格式化 ----
function escapeHtml(value) {
  const text = String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
  return text;
}

// 调用 API 并返回 JSON；失败时抛出带后端 detail 的错误，页面直接显示原因。
async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return body;
}

async function postJson(url, body) {
  const result = await fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return result;
}

function formatCount(value) {
  const text = Number(value ?? 0).toLocaleString();
  return text;
}

function formatBytes(bytes) {
  const text = bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${(bytes / 1e6).toFixed(1)} MB`;
  return text;
}

function baseName(path) {
  const name = String(path ?? "").split(/[\\/]/).filter(Boolean).pop() ?? "";
  return name;
}

// ---- 通用：提示、加载、统计卡片、详情 ----
const alertColors = {
  error: "border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-900/20 dark:text-red-400",
  warning: "border-yellow-200 bg-yellow-50 text-yellow-800 dark:border-yellow-900 dark:bg-yellow-900/20 dark:text-yellow-400",
  success: "border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-900 dark:bg-teal-900/20 dark:text-teal-400",
  info: "border-gray-200 bg-gray-50 text-gray-700 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300",
};

// text 会被转义；extraHtml 为可信的附加内容（如链接按钮），不转义。
function alertHtml(text, kind, extraHtml = "") {
  const role = kind === "error" ? "alert" : "status";
  const html = `<div class="rounded-lg border px-3 py-2 text-sm ${alertColors[kind]}" role="${role}">${escapeHtml(text)}${extraHtml}</div>`;
  return html;
}

function loadingHtml(text = "加载中…") {
  const html = `<div class="flex items-center gap-2 py-2 text-sm text-gray-500 dark:text-neutral-400" role="status">
    <span class="size-4 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none" aria-hidden="true"></span>${escapeHtml(text)}</div>`;
  return html;
}

function emptyHtml(text) {
  const html = `<div class="rounded-lg border border-dashed border-gray-300 px-3 py-6 text-center text-sm text-gray-500 dark:border-neutral-600 dark:text-neutral-400">${escapeHtml(text)}</div>`;
  return html;
}

// items 为 [[标签, 值], ...]；值会被转义。
function statCards(items) {
  const cards = items.map(([label, value]) => `
    <div class="rounded-lg border border-gray-200 px-3 py-2 dark:border-neutral-700">
      <div class="text-xs text-gray-500 dark:text-neutral-400">${escapeHtml(label)}</div>
      <div class="mt-0.5 truncate text-base font-semibold text-gray-800 dark:text-white" title="${escapeHtml(value)}">${escapeHtml(value)}</div>
    </div>`);
  const html = `<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">${cards.join("")}</div>`;
  return html;
}

// innerHtml 为可信 HTML。
function detailsHtml(summary, innerHtml, open = false) {
  const html = `<details class="ui-details"${open ? " open" : ""}><summary class="ui-details-summary">${escapeHtml(summary)}</summary><div class="border-t border-gray-200 p-3 dark:border-neutral-700">${innerHtml}</div></details>`;
  return html;
}

// headers 为列名（转义）；rows 为可信的 <tr> HTML（单元格用 ui-td）。表头随内容滚动时固定。
function tableHtml(headers, rows, scrollClass = "") {
  const head = headers.map((header) => `<th scope="col" class="ui-th sticky top-0 bg-gray-50 dark:bg-neutral-900">${escapeHtml(header)}</th>`).join("");
  const html = `
    <div class="overflow-auto rounded-lg border border-gray-200 dark:border-neutral-700 ${scrollClass}">
      <table class="ui-table"><thead><tr>${head}</tr></thead>
        <tbody class="divide-y divide-gray-200 dark:divide-neutral-700">${rows.join("")}</tbody></table>
    </div>`;
  return html;
}

function listHtml(items) {
  const html = `<ul class="list-disc space-y-0.5 ps-5">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
  return html;
}

// 预检结果首行：通过时显示一句摘要，未通过时列出全部阻塞原因。
function previewStatusHtml(preview, passText) {
  if (preview.ready) return alertHtml(`预检通过：${passText}`, "success");
  const html = `<div class="rounded-lg border px-3 py-2 text-sm ${alertColors.error}" role="alert"><div class="font-medium">预检未通过</div>${listHtml(preview.blocking)}</div>`;
  return html;
}

function outputHtml(preview) {
  const state = preview.output_exists ? "已存在，不会覆盖" : "将新建";
  const html = `<p class="ui-hint">输出：<span class="font-mono">${escapeHtml(preview.output_path)}</span>（${state}，剩余 ${formatBytes(preview.free_bytes)}）</p>`;
  return html;
}

// ---- 数据集下拉框 ----
const datasetCache = new Map();

function datasetById(id) {
  const dataset = datasetCache.get(Number(id));
  return dataset;
}

// 填入已登记的数据集（formats 为空表示全部格式），尽量保留原选择；返回选中项是否变化。
async function fillDatasetSelect(select, formats = null) {
  const previous = select.value;
  if (!select.options.length) select.innerHTML = "<option value=''>加载中…</option>";
  const datasets = (await fetchJson("/api/datasets")).filter((dataset) => !formats || formats.includes(dataset.format));
  datasets.forEach((dataset) => datasetCache.set(dataset.id, dataset));
  // 只列一种格式时不重复显示格式。
  const showFormat = !(formats && formats.length === 1);
  select.innerHTML = datasets.length
    ? datasets.map((dataset) => `<option value="${dataset.id}">${escapeHtml(dataset.name)}${showFormat ? `（${formatLabel(dataset.format)}）` : ""}</option>`).join("")
    : `<option value="">暂无可用数据集，请先在“数据管理”中添加</option>`;
  select.disabled = !datasets.length;
  if (datasets.some((dataset) => String(dataset.id) === previous)) select.value = previous;
  const changed = select.value !== previous;
  return changed;
}

function formatLabel(format) {
  const labels = { h5: "H5", "lerobot_v3.0": "v3.0", "lerobot_v2.1": "v2.1" };
  const label = labels[format] ?? format;
  return label;
}

// ---- 后台任务状态 ----
const taskStatusLabels = { queued: "排队中", running: "运行中", validating: "验证中", cancelling: "取消中", succeeded: "完成", failed: "失败", cancelled: "已取消", interrupted: "已中断" };
const activeTaskStatuses = ["queued", "running", "validating", "cancelling"];
const taskPollTimers = {};

function taskReason(task) {
  // error 为完整回溯，只取最后一行（异常信息）。
  const reason = (task.error || "").trim().split("\n").pop() || task.message || "";
  return reason;
}

// 统计帧数任务的结果（格式转换页与后台任务页共用）。
function frameCountResultText(result) {
  const depth = result.depth_misaligned_episodes.length ? `${result.depth_misaligned_episodes.length} 个 episode 的 RGB 与深度未对齐` : "RGB 与深度全部对齐";
  const zero = result.zero_frame_episodes.length ? `，${result.zero_frame_episodes.length} 个 episode 没有输出帧` : "";
  const text = `${result.episode_count} 个 episode，${formatCount(result.images)} 张图像，输出 ${formatCount(result.output_frames)} 帧；${depth}${zero}。`;
  return text;
}

function taskStatusHtml(task, resultText) {
  const label = taskStatusLabels[task.status] || task.status;
  if (task.status === "succeeded") {
    const open = task.result?.registered_dataset_id ? ` <button type="button" class="ui-link" data-open-dataset="${task.result.registered_dataset_id}">查看数据集</button>` : "";
    return alertHtml(`完成：${resultText(task.result)}`, "success", open);
  }
  if (!activeTaskStatuses.includes(task.status)) {
    return alertHtml(`任务 #${task.id} ${label}：${taskReason(task)}`, "error", ` <button type="button" class="ui-link" data-open-panel="task">查看后台任务</button>`);
  }
  const total = task.progress_total || 0;
  const percent = total ? Math.round((100 * task.progress_done) / total) : 0;
  const html = `
    <div class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-neutral-700" role="status">
      <div class="flex justify-between gap-3"><span>任务 #${task.id} · ${label}</span><span class="text-gray-500 dark:text-neutral-400">${total ? `${percent}%` : ""}</span></div>
      <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-200 dark:bg-neutral-700" role="progressbar" aria-label="进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}">
        <div class="h-full rounded-full bg-blue-600 transition-[width]" style="width: ${total ? percent : 5}%"></div>
      </div>
      <p class="ui-hint mt-1">${escapeHtml(task.message || "")}</p>
    </div>`;
  return html;
}

// 每秒刷新任务状态到 target，直到结束；成功时调用 onSucceeded(task)。
async function pollTaskInto(taskId, target, resultText, onSucceeded) {
  clearTimeout(taskPollTimers[target.id]);
  try {
    const task = await fetchJson(`/api/tasks/${taskId}`);
    target.innerHTML = taskStatusHtml(task, resultText);
    if (activeTaskStatuses.includes(task.status)) {
      taskPollTimers[target.id] = setTimeout(() => pollTaskInto(taskId, target, resultText, onSucceeded), 1000);
    } else if (task.status === "succeeded" && onSucceeded) {
      onSucceeded(task);
    }
  } catch (error) {
    target.innerHTML = alertHtml(error.message, "error");
  }
}

// ---- 加工页共用流程：预检 → 生成 → 轮询任务 ----
// request() 返回请求体（返回 null 表示输入不完整，不发请求）；preview / submit 发请求并返回 Promise；
// renderPreview(preview) 返回预检结果 HTML；resultText(result) 为任务完成时的一句话。设置变化时调用 flow.reset() 要求重新预检。
function setupFlow(options) {
  const { previewButton, submitButton, result, status } = options;
  const flow = {
    reset() {
      result.innerHTML = "";
      submitButton.disabled = true;
    },
  };
  previewButton.addEventListener("click", async () => {
    const request = options.request();
    if (!request) return;
    previewButton.disabled = true;
    submitButton.disabled = true;
    result.innerHTML = loadingHtml("预检中…");
    try {
      const preview = await options.preview(request);
      result.innerHTML = options.renderPreview(preview);
      submitButton.disabled = !preview.ready;
    } catch (error) {
      result.innerHTML = alertHtml(error.message, "error");
    } finally {
      previewButton.disabled = false;
    }
  });
  submitButton.addEventListener("click", async () => {
    submitButton.disabled = true;
    status.innerHTML = loadingHtml("提交中…");
    try {
      const task = await options.submit(options.request());
      pollTaskInto(task.id, status, options.resultText, options.onSucceeded);
    } catch (error) {
      status.innerHTML = alertHtml(error.message, "error");
    }
  });
  return flow;
}

// ---- 导航 ----
const panelContainer = document.querySelector("#panel-container");
const navButtons = [...document.querySelectorAll("[data-nav-target]")];
const panels = [...document.querySelectorAll("[data-panel]")];

function showPanel(name, updateHash = true) {
  const selected = panels.find((panel) => panel.dataset.panel === name) ?? panels[0];
  panels.forEach((panel) => {
    panel.hidden = panel !== selected;
  });
  navButtons.forEach((button) => {
    const active = button.dataset.navTarget === selected.dataset.panel;
    button.setAttribute("aria-current", active ? "page" : "false");
    if (active) {
      document.querySelector("#breadcrumb-group").textContent = button.dataset.navGroup;
      document.querySelector("#breadcrumb-current").textContent = button.dataset.navLabel;
      document.title = `${button.dataset.navLabel} · 机器人数据平台`;
    }
  });
  // 小屏下侧边栏是 Preline offcanvas，选择页面后关闭；大屏常驻不受影响。
  const sidebar = document.querySelector("#app-sidebar");
  if (window.HSOverlay && sidebar.classList.contains("open")) window.HSOverlay.close(sidebar);
  if (updateHash) history.replaceState(null, "", `#${selected.dataset.panel}`);
  panelContainer.focus({ preventScroll: true });
  document.dispatchEvent(new CustomEvent("panel:show", { detail: selected.dataset.panel }));
}

// 各页面在显示时加载数据（数据集列表等可能已在其他页面变化）。
function onPanelShow(name, callback) {
  document.addEventListener("panel:show", (event) => {
    if (event.detail === name) callback();
  });
}

function openDataset(datasetId) {
  showPanel("dataset");
  document.dispatchEvent(new CustomEvent("dataset:open", { detail: Number(datasetId) }));
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => showPanel(button.dataset.navTarget));
});

// 结果中的“查看数据集”“查看后台任务”等链接按钮。
document.addEventListener("click", (event) => {
  const openDatasetButton = event.target.closest("[data-open-dataset]");
  if (openDatasetButton) openDataset(openDatasetButton.dataset.openDataset);
  const openPanelButton = event.target.closest("[data-open-panel]");
  if (openPanelButton) showPanel(openPanelButton.dataset.openPanel);
});

// 各页面脚本（defer，按顺序执行）注册完 onPanelShow 后再显示初始页面。
document.addEventListener("DOMContentLoaded", () => showPanel(window.location.hash.slice(1), false));

const themeKey = "manip-data-theme";
document.querySelector("#theme-toggle").addEventListener("click", () => {
  const dark = document.documentElement.classList.toggle("dark");
  localStorage.setItem(themeKey, dark ? "dark" : "light");
});

function setServiceStatus(ok) {
  document.querySelector("#service-status").className = `inline-block size-2 rounded-full ${ok ? "bg-teal-500" : "bg-red-500"}`;
  document.querySelector("#service-status-text").textContent = ok ? "服务正常" : "服务异常";
}

fetchJson("/api/health")
  .then(() => setServiceStatus(true))
  .catch(() => setServiceStatus(false));
