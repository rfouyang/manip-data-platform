const taskPageList = document.querySelector("#task-page-list");
const taskPageSummary = document.querySelector("#task-page-summary");
const taskPageFilters = document.querySelector("#task-page-filters");
const taskPageKind = document.querySelector("#task-page-kind");
const taskPageMessage = document.querySelector("#task-page-message");

// 任务类型名称与导航页面一致。
const taskPageKinds = {
  conversion_frame_count: "统计帧数",
  conversion_h5_to_lerobot_v3: "格式转换 H5 → v3.0",
  conversion_h5_to_lerobot_v21: "格式转换 H5 → v2.1",
  conversion_lerobot_v3_to_v21: "格式转换 v3.0 → v2.1",
  conversion_lerobot_v21_to_v3: "格式转换 v2.1 → v3.0",
  tag_column: "添加标签",
  dataset_edit_tag: "添加标签",
  split_subset: "数据拆分",
  merge_datasets: "数据合并",
  subtask_write: "任务标注：生成",
  subtask_to_task: "任务标注：转逐帧",
};
const taskPageBadges = {
  queued: "bg-gray-100 text-gray-700 dark:bg-neutral-700 dark:text-neutral-200",
  running: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  validating: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  cancelling: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
  succeeded: "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  cancelled: "bg-gray-100 text-gray-600 dark:bg-neutral-700 dark:text-neutral-300",
  interrupted: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
};
// 状态筛选：进行中含排队与取消中；问题含失败与中断。
const taskPageGroups = {
  all: ["全部", null],
  active: ["进行中", activeTaskStatuses],
  problem: ["失败", ["failed", "interrupted"]],
  succeeded: ["完成", ["succeeded"]],
  cancelled: ["已取消", ["cancelled"]],
};
const taskPageRetryable = ["failed", "cancelled", "interrupted"];
// datasetIds：当前仍登记的数据集，已删除登记的不显示“查看数据集”。
const taskPageState = { tasks: [], datasetIds: new Set(), group: "all", expanded: new Set(), timer: null };

// 来源取数据集目录名（合并为多个来源），输出取目录名。
function taskPageSourceOutput(task) {
  const params = task.params || {};
  const sources = params.sources ? params.sources.map((source) => source.name) : [baseName(params.dataset_path)];
  const output = params.output_path ? baseName(params.output_path) : "";
  const html = `<div>${escapeHtml(sources.filter(Boolean).join(" + ") || "—")}</div>`
    + (output ? `<div class="ui-hint">→ ${escapeHtml(output)}</div>` : "");
  return html;
}

function taskPageDuration(task) {
  if (!task.started_at) return "";
  const end = task.finished_at ? new Date(task.finished_at) : new Date();
  const seconds = Math.max(0, Math.round((end - new Date(task.started_at)) / 1000));
  const text = seconds >= 3600 ? `${Math.floor(seconds / 3600)} 时 ${Math.floor((seconds % 3600) / 60)} 分`
    : seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
  return text;
}

// 结果摘要：成功时为帧数与验证项数（带“查看数据集”），失败时为异常的最后一行。
function taskPageOutcome(task) {
  if (activeTaskStatuses.includes(task.status)) return "";
  const result = task.result;
  if (task.status === "succeeded" && result) {
    if (task.kind === "conversion_frame_count") return escapeHtml(frameCountResultText(result));
    const size = result.episode_count !== undefined ? `${formatCount(result.episode_count)} 个 episode${result.frames !== undefined ? `、${formatCount(result.frames)} 帧` : ""}，` : "";
    const text = `${size}验证 ${result.validation?.check_count ?? 0} 项通过`;
    const open = taskPageState.datasetIds.has(result.registered_dataset_id) ? ` <button type="button" class="ui-link" data-open-dataset="${result.registered_dataset_id}">查看数据集</button>` : "";
    return escapeHtml(text) + open;
  }
  const color = ["failed", "interrupted"].includes(task.status) ? "text-red-700 dark:text-red-400" : "text-gray-600 dark:text-neutral-400";
  const html = `<span class="${color}">${escapeHtml(taskReason(task))}</span>`;
  return html;
}

function taskPageDetail(task) {
  const failed = task.result?.validation?.failed || [];
  const blocks = [];
  if (task.error) blocks.push(`<div><div class="ui-hint mb-1 font-medium">错误详情</div><pre class="max-h-64 overflow-auto rounded-md bg-gray-900 p-3 text-xs text-gray-100">${escapeHtml(task.error)}</pre></div>`);
  if (task.result?.validation) {
    const count = task.result.validation.failed_count ?? failed.length;
    const items = failed.length ? listHtml(failed.map((item) => `${item.name} ${item.detail || ""}`)) : "全部通过";
    blocks.push(`<div class="text-sm"><span class="font-medium">验证：</span>${task.result.validation.check_count} 项${count ? `，${count} 项未通过` : ""}，${items}</div>`);
  }
  if (task.result?.output_path) blocks.push(`<div class="text-sm"><span class="font-medium">输出：</span><span class="font-mono text-xs select-all">${escapeHtml(task.result.output_path)}</span></div>`);
  blocks.push(`<div><div class="ui-hint mb-1 font-medium">参数</div><pre class="max-h-48 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700 dark:bg-neutral-900 dark:text-neutral-300">${escapeHtml(JSON.stringify(task.params, null, 2))}</pre></div>`);
  const html = `<tr class="bg-gray-50/60 dark:bg-neutral-900/40"><td colspan="6" class="px-4 py-3"><div class="space-y-3">${blocks.join("")}</div></td></tr>`;
  return html;
}

function taskPageRow(task) {
  const label = taskStatusLabels[task.status] || task.status;
  const active = activeTaskStatuses.includes(task.status);
  // 成功的任务按完成显示（最后一次进度在验证之前上报）。
  const percent = task.status === "succeeded" ? 100 : task.progress_total ? Math.round((100 * task.progress_done) / task.progress_total) : 0;
  const expanded = taskPageState.expanded.has(task.id);
  const actions = [
    active && task.status !== "cancelling" ? `<button type="button" class="ui-btn ui-btn-sm" data-task-page-action="cancel" data-task-id="${task.id}">取消</button>` : "",
    taskPageRetryable.includes(task.status) ? `<button type="button" class="ui-btn ui-btn-sm" data-task-page-action="retry" data-task-id="${task.id}">重试</button>` : "",
    `<button type="button" class="ui-btn ui-btn-sm" data-task-page-detail="${task.id}" aria-expanded="${expanded}">${expanded ? "收起" : "详情"}</button>`,
  ].join(" ");
  const retryOf = task.retry_of ? `<div class="ui-hint">重试自 #${task.retry_of}</div>` : "";
  const progress = active
    ? `<div class="h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-neutral-700" role="progressbar" aria-label="进度" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><div class="h-full bg-blue-600" style="width: ${percent}%"></div></div>
       <div class="ui-hint mt-1">${escapeHtml(task.message || "")} · ${task.progress_done} / ${task.progress_total || "?"}</div>`
    : "";
  const row = `
    <tr class="align-top">
      <td class="ui-td whitespace-nowrap font-mono">#${task.id}${retryOf}</td>
      <td class="ui-td"><div class="whitespace-nowrap">${escapeHtml(taskPageKinds[task.kind] || task.kind)}</div>${taskPageSourceOutput(task)}</td>
      <td class="ui-td"><span class="ui-badge ${taskPageBadges[task.status] || taskPageBadges.queued}">${label}</span></td>
      <td class="ui-td min-w-60">${progress}<div class="text-sm">${taskPageOutcome(task)}</div></td>
      <td class="ui-td whitespace-nowrap"><div class="ui-hint">${escapeHtml((task.created_at || "").replace("T", " "))}</div><div class="ui-hint">${taskPageDuration(task)}</div></td>
      <td class="ui-td whitespace-nowrap">${actions}</td>
    </tr>`;
  const html = row + (expanded ? taskPageDetail(task) : "");
  return html;
}

function renderTaskPageSummary() {
  const count = (statuses) => taskPageState.tasks.filter((task) => statuses.includes(task.status)).length;
  taskPageSummary.innerHTML = statCards([
    ["进行中", count(activeTaskStatuses)],
    ["失败 / 中断", count(["failed", "interrupted"])],
    ["完成", count(["succeeded"])],
    ["已取消", count(["cancelled"])],
  ]);
}

function renderTaskPageFilters() {
  taskPageFilters.innerHTML = Object.entries(taskPageGroups).map(([key, [label]]) => `
    <label class="ui-segment-item"><input type="radio" name="task-page-group" value="${key}" class="sr-only" ${key === taskPageState.group ? "checked" : ""} />${label}</label>`).join("");
  const previous = taskPageKind.value;
  const kinds = [...new Set(taskPageState.tasks.map((task) => task.kind))];
  taskPageKind.innerHTML = `<option value="">全部类型</option>` + kinds.map((kind) => `<option value="${escapeHtml(kind)}">${escapeHtml(taskPageKinds[kind] || kind)}</option>`).join("");
  if (kinds.includes(previous)) taskPageKind.value = previous;
}

function renderTaskPageList() {
  const statuses = taskPageGroups[taskPageState.group][1];
  const tasks = taskPageState.tasks.filter((task) => (!statuses || statuses.includes(task.status)) && (!taskPageKind.value || task.kind === taskPageKind.value));
  const head = ["编号", "类型 / 数据", "状态", "进度 / 结果", "提交 / 耗时", "操作"].map((header) => `<th scope="col" class="ui-th">${header}</th>`).join("");
  taskPageList.innerHTML = tasks.length
    ? `<table class="ui-table"><thead class="bg-gray-50 dark:bg-neutral-900"><tr>${head}</tr></thead>
        <tbody class="divide-y divide-gray-200 dark:divide-neutral-700">${tasks.map(taskPageRow).join("")}</tbody></table>`
    : `<div class="p-4">${emptyHtml(taskPageState.tasks.length ? "没有符合条件的任务。" : "还没有任务。在加工页面预检并生成后，任务会显示在这里。")}</div>`;
}

async function loadTaskPage() {
  clearTimeout(taskPageState.timer);
  if (!taskPageState.tasks.length) taskPageList.innerHTML = `<div class="p-4">${loadingHtml()}</div>`;
  try {
    const [tasks, datasets] = await Promise.all([fetchJson("/api/tasks?limit=200"), fetchJson("/api/datasets")]);
    taskPageState.tasks = tasks;
    taskPageState.datasetIds = new Set(datasets.map((dataset) => dataset.id));
    renderTaskPageSummary();
    renderTaskPageFilters();
    renderTaskPageList();
    // 有未结束任务且页面可见时每秒刷新。
    const panelVisible = !taskPageList.closest("[data-panel]").hidden;
    if (panelVisible && taskPageState.tasks.some((task) => activeTaskStatuses.includes(task.status))) taskPageState.timer = setTimeout(loadTaskPage, 1000);
  } catch (error) {
    taskPageMessage.innerHTML = alertHtml(error.message, "error");
  }
}

taskPageFilters.addEventListener("change", (event) => {
  taskPageState.group = event.target.value;
  renderTaskPageList();
});
taskPageKind.addEventListener("change", renderTaskPageList);
taskPageList.addEventListener("click", async (event) => {
  const detail = event.target.closest("[data-task-page-detail]");
  if (detail) {
    const id = Number(detail.dataset.taskPageDetail);
    if (taskPageState.expanded.has(id)) taskPageState.expanded.delete(id);
    else taskPageState.expanded.add(id);
    renderTaskPageList();
    taskPageList.querySelector(`[data-task-page-detail="${id}"]`)?.focus();
    return;
  }
  const action = event.target.closest("[data-task-page-action]");
  if (!action) return;
  action.disabled = true;
  taskPageMessage.innerHTML = "";
  try {
    const task = await fetchJson(`/api/tasks/${action.dataset.taskId}/${action.dataset.taskPageAction}`, { method: "POST" });
    if (action.dataset.taskPageAction === "retry") taskPageMessage.innerHTML = alertHtml(`已创建重试任务 #${task.id}。`, "success");
  } catch (error) {
    taskPageMessage.innerHTML = alertHtml(error.message, "error");
  }
  loadTaskPage();
});
document.querySelector("#task-page-refresh").addEventListener("click", loadTaskPage);
onPanelShow("task", loadTaskPage);
