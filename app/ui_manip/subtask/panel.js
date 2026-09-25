const subtaskDataset = document.querySelector("#subtask-dataset");
const subtaskCounts = document.querySelector("#subtask-counts");
const subtaskMessage = document.querySelector("#subtask-message");
const subtaskEpisodes = document.querySelector("#subtask-episodes");
const subtaskEditor = document.querySelector("#subtask-editor");
const subtaskEmpty = document.querySelector("#subtask-empty");
const subtaskCamera = document.querySelector("#subtask-camera");
const subtaskImage = document.querySelector("#subtask-image");
const subtaskSlider = document.querySelector("#subtask-slider");
const subtaskCurrent = document.querySelector("#subtask-current");
const subtaskTimeline = document.querySelector("#subtask-timeline");
const subtaskSegmentsBox = document.querySelector("#subtask-segments");
const subtaskEditorMessage = document.querySelector("#subtask-editor-message");
const subtaskTasksBox = document.querySelector("#subtask-tasks");
const subtaskCommon = document.querySelector("#subtask-common");
const subtaskSuggest = document.querySelector("#subtask-suggest");

const subtaskStatusLabels = {
  modified: ["已修改", "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300"],
  labeled: ["已标注", "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300"],
  unlabeled: ["未标注", "bg-gray-100 text-gray-600 dark:bg-neutral-700 dark:text-neutral-300"],
};
const subtaskColors = ["bg-blue-200 dark:bg-blue-900", "bg-teal-200 dark:bg-teal-900", "bg-amber-200 dark:bg-amber-900", "bg-purple-200 dark:bg-purple-900", "bg-rose-200 dark:bg-rose-900"];

// 当前编辑的 episode：segments 为页面上未保存的分段 [{start, text}]。
const subtaskState = { episode: null, segments: [], tasks: [], frame: 0, dirty: false, imageTimer: null };
// 本数据集已用过的子任务描述（服务端统计，按使用段数降序），用于常用标签与输入下拉建议。
let subtaskUsedTexts = [];
const subtaskSuggestState = { input: null, items: [], active: -1 };

function subtaskBase() {
  const base = `/api/subtask/${subtaskDataset.value}`;
  return base;
}

function subtaskBadge(status) {
  const [label, color] = subtaskStatusLabels[status];
  const html = `<span class="ui-badge ${color}">${label}</span>`;
  return html;
}

async function loadSubtaskEpisodes() {
  subtaskCounts.textContent = "";
  if (!subtaskDataset.value) {
    subtaskEpisodes.innerHTML = "";
    return;
  }
  if (!subtaskEpisodes.children.length) subtaskEpisodes.innerHTML = `<div class="px-4">${loadingHtml()}</div>`;
  try {
    const summary = await fetchJson(`${subtaskBase()}/episodes`);
    const counts = summary.counts;
    subtaskCounts.textContent = `${summary.episodes.length} 个 episode · 已修改 ${counts.modified} · 已标注 ${counts.labeled} · 未标注 ${counts.unlabeled}`;
    subtaskUsedTexts = summary.subtask_texts;
    if (subtaskState.episode) renderSubtaskCommon();
    subtaskEpisodes.innerHTML = summary.episodes.map((item) => {
      const active = subtaskState.episode?.episode_index === item.episode_index;
      const row = `
        <button type="button" data-episode="${item.episode_index}" aria-current="${active}"
          class="flex w-full items-center gap-2 border-b border-gray-100 px-4 py-2 text-start text-sm hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-hidden aria-[current=true]:bg-blue-50 dark:border-neutral-700 dark:hover:bg-neutral-700 dark:aria-[current=true]:bg-blue-900/30">
          <span class="font-mono">${item.episode_index}</span>
          <span class="ui-hint">${item.length} 帧 · ${item.segments} 段</span>
          <span class="ms-auto">${subtaskBadge(item.status)}</span>
        </button>`;
      return row;
    }).join("");
  } catch (error) {
    subtaskEpisodes.innerHTML = `<div class="p-3">${alertHtml(error.message, "error")}</div>`;
  }
}

async function openSubtaskEpisode(episodeIndex) {
  if (subtaskState.dirty && !window.confirm("当前 episode 有未保存的修改，放弃并切换？")) return;
  subtaskEditorMessage.innerHTML = "";
  try {
    const detail = await fetchJson(`${subtaskBase()}/episodes/${episodeIndex}`);
    const episodeId = `episode_${String(episodeIndex).padStart(6, "0")}`;
    const info = await fetchJson(`/api/visualization/episode?dataset_id=${subtaskDataset.value}&episode_id=${episodeId}`);
    const previousCamera = subtaskCamera.value;
    subtaskCamera.innerHTML = Object.keys(info.cameras).map((camera) => `<option value="${escapeHtml(camera)}">${escapeHtml(camera)}</option>`).join("");
    if (info.cameras[previousCamera]) subtaskCamera.value = previousCamera;
    Object.assign(subtaskState, { episode: { ...detail, episode_id: episodeId }, segments: detail.segments.map(({ start, text }) => ({ start, text })), tasks: [...detail.edited_tasks], frame: 0, dirty: false });
    document.querySelector("#subtask-episode-title").textContent = `Episode ${episodeIndex} · ${detail.length} 帧 · ${detail.fps} FPS`;
    renderSubtaskTasks();
    subtaskSlider.max = detail.length - 1;
    subtaskEmpty.hidden = true;
    subtaskEditor.hidden = false;
    subtaskEpisodes.querySelectorAll("[data-episode]").forEach((button) => {
      button.setAttribute("aria-current", String(Number(button.dataset.episode) === episodeIndex));
    });
    setSubtaskFrame(0);
    renderSubtaskSegments();
  } catch (error) {
    subtaskMessage.innerHTML = alertHtml(error.message, "error");
  }
}

// episode 的任务描述：每个原描述一个输入框，改动后显示原文。
function renderSubtaskTasks() {
  const original = subtaskState.episode.tasks;
  subtaskTasksBox.innerHTML = subtaskState.tasks.map((task, index) => {
    const changed = task !== original[index] ? `<span class="ui-hint ms-2 text-yellow-700 dark:text-yellow-400">原：${escapeHtml(original[index])}</span>` : "";
    const html = `
      <div>
        <label class="ui-label" for="subtask-task-${index}">任务描述${original.length > 1 ? ` ${index + 1}` : ""}${changed}</label>
        <input id="subtask-task-${index}" value="${escapeHtml(task)}" data-task="${index}" class="ui-input" />
      </div>`;
    return html;
  }).join("");
}

// 当前帧所在的段（起始帧不大于当前帧的最后一段）。
function subtaskSegmentAt(frame) {
  let position = -1;
  subtaskState.segments.forEach((segment, index) => {
    if (segment.start <= frame) position = index;
  });
  return position;
}

function setSubtaskFrame(frame) {
  const episode = subtaskState.episode;
  subtaskState.frame = Math.max(0, Math.min(episode.length - 1, frame));
  subtaskSlider.value = subtaskState.frame;
  const position = subtaskSegmentAt(subtaskState.frame);
  const text = position >= 0 ? subtaskState.segments[position].text || "（空）" : "（无）";
  subtaskCurrent.innerHTML = `帧 <span class="font-mono">${subtaskState.frame}</span> · ${(subtaskState.frame / episode.fps).toFixed(2)} s · 子任务：<span class="font-medium">${escapeHtml(text)}</span>`;
  subtaskTimeline.querySelectorAll("[data-segment]").forEach((element) => element.classList.toggle("ring-2", Number(element.dataset.segment) === position));
  // 拖动滑块时只加载最后停下的帧。
  clearTimeout(subtaskState.imageTimer);
  subtaskState.imageTimer = setTimeout(() => {
    if (!subtaskCamera.value) return;
    subtaskImage.src = `/api/visualization/color?dataset_id=${subtaskDataset.value}&episode_id=${episode.episode_id}&camera=${encodeURIComponent(subtaskCamera.value)}&index=${subtaskState.frame}`;
  }, 80);
}

function renderSubtaskTimeline() {
  const length = subtaskState.episode.length;
  const segments = subtaskState.segments;
  subtaskTimeline.innerHTML = segments.length
    ? segments.map((segment, index) => {
      const end = index + 1 < segments.length ? segments[index + 1].start : length;
      const width = ((end - segment.start) / length) * 100;
      const html = `<button type="button" data-segment="${index}" title="${escapeHtml(segment.text)}" aria-label="跳到第 ${index + 1} 段：${escapeHtml(segment.text)}" style="width:${width}%" class="${subtaskColors[index % subtaskColors.length]} truncate px-1 text-start text-gray-800 ring-blue-600 ring-inset focus-visible:ring-2 focus-visible:outline-hidden dark:text-neutral-100">${escapeHtml(segment.text)}</button>`;
      return html;
    }).join("")
    : `<div class="flex w-full items-center px-2 text-gray-500 dark:text-neutral-400">还没有子任务，点“在此帧切分”开始。</div>`;
}

function renderSubtaskSegments() {
  const length = subtaskState.episode.length;
  const segments = subtaskState.segments;
  const rows = segments.map((segment, index) => {
    const end = index + 1 < segments.length ? segments[index + 1].start - 1 : length - 1;
    const startInput = index === 0
      ? `<span class="font-mono">0</span>`
      : `<input type="number" min="1" max="${length - 1}" value="${segment.start}" data-start="${index}" class="ui-input w-24 py-1 font-mono" aria-label="第 ${index + 1} 段起始帧" />`;
    const row = `
      <tr>
        <td class="ui-td"><span class="inline-block size-3 rounded-sm ${subtaskColors[index % subtaskColors.length]}"></span></td>
        <td class="ui-td">${startInput}</td>
        <td class="ui-td font-mono">${end}</td>
        <td class="ui-td whitespace-nowrap">${((end - segment.start + 1) / subtaskState.episode.fps).toFixed(2)} s</td>
        <td class="ui-td w-full min-w-48"><input value="${escapeHtml(segment.text)}" data-text="${index}" class="ui-input py-1" placeholder="子任务描述" aria-label="第 ${index + 1} 段子任务" /></td>
        <td class="ui-td whitespace-nowrap">
          <button type="button" data-jump="${index}" class="ui-link">跳转</button>
          ${index > 0 ? `<button type="button" data-remove="${index}" class="ui-link ms-3 text-red-600 dark:text-red-400">并入上段</button>` : ""}
        </td>
      </tr>`;
    return row;
  });
  subtaskSegmentsBox.innerHTML = segments.length ? tableHtml(["", "起始帧", "结束帧", "时长", "子任务", ""], rows) : "";
  renderSubtaskTimeline();
  renderSubtaskCommon();
  setSubtaskFrame(subtaskState.frame);
}

// 常用子任务：已保存的按使用次数排序，再补上当前 episode 里新输入、尚未保存的描述。
function subtaskVocabulary() {
  const texts = subtaskUsedTexts.map((item) => ({ ...item }));
  subtaskState.segments.forEach((segment) => {
    const text = segment.text.trim();
    if (text && !texts.some((item) => item.text === text)) texts.push({ text, count: 0 });
  });
  return texts;
}

function renderSubtaskCommon() {
  const texts = subtaskVocabulary().slice(0, 9);
  subtaskCommon.innerHTML = texts.length
    ? `<span class="ui-hint font-medium">常用子任务</span>` + texts.map((item, index) => `
      <button type="button" data-common="${index}" title="${escapeHtml(item.text)}（按 ${index + 1}）"
        class="inline-flex max-w-full items-center gap-1.5 rounded-full border border-gray-200 bg-white py-1 ps-1 pe-3 text-xs hover:border-blue-300 hover:bg-blue-50 focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:outline-hidden dark:border-neutral-700 dark:bg-neutral-900 dark:hover:bg-neutral-700">
        <span class="inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-gray-100 font-mono text-[11px] text-gray-600 dark:bg-neutral-700 dark:text-neutral-300">${index + 1}</span>
        <span class="truncate">${escapeHtml(item.text)}</span>
        ${item.count ? `<span class="text-gray-500 dark:text-neutral-400">${item.count}</span>` : ""}
      </button>`).join("")
    : `<span class="ui-hint">保存过的子任务会出现在这里，可点击或按数字键填入。</span>`;
}

// 把描述填入当前帧所在的段；还没有分段时先新建从第 0 帧开始的一段。
function fillSubtaskAtFrame(text) {
  if (!subtaskState.segments.length) subtaskState.segments.push({ start: 0, text: "" });
  const position = subtaskSegmentAt(subtaskState.frame);
  subtaskState.segments[position].text = text;
  markSubtaskDirty();
  renderSubtaskSegments();
}

function closeSubtaskSuggest() {
  subtaskSuggest.hidden = true;
  Object.assign(subtaskSuggestState, { input: null, items: [], active: -1 });
}

// 描述输入框的下拉建议：按输入内容过滤常用子任务（不区分大小写），显示在输入框下方。
function openSubtaskSuggest(input) {
  const query = input.value.trim().toLowerCase();
  const items = subtaskVocabulary().filter((item) => item.text !== input.value.trim() && item.text.toLowerCase().includes(query)).slice(0, 8);
  if (!items.length) {
    closeSubtaskSuggest();
    return;
  }
  Object.assign(subtaskSuggestState, { input, items, active: -1 });
  const rect = input.getBoundingClientRect();
  Object.assign(subtaskSuggest.style, { left: `${rect.left}px`, top: `${rect.bottom + 4}px`, width: `${rect.width}px` });
  renderSubtaskSuggest();
  subtaskSuggest.hidden = false;
}

function renderSubtaskSuggest() {
  subtaskSuggest.innerHTML = subtaskSuggestState.items.map((item, index) => {
    const active = index === subtaskSuggestState.active ? "bg-gray-100 dark:bg-neutral-700" : "";
    const html = `
      <div role="option" data-suggest="${index}" aria-selected="${index === subtaskSuggestState.active}"
        class="flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-neutral-700 ${active}">
        <span class="truncate">${escapeHtml(item.text)}</span>
        <span class="ms-auto shrink-0 text-xs text-gray-500 dark:text-neutral-400">${item.count ? `${item.count} 段` : "新"}</span>
      </div>`;
    return html;
  }).join("");
}

function pickSubtaskSuggest(index) {
  const input = subtaskSuggestState.input;
  input.value = subtaskSuggestState.items[index].text;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  closeSubtaskSuggest();
}

function markSubtaskDirty() {
  subtaskState.dirty = true;
  subtaskEditorMessage.innerHTML = alertHtml("有未保存的修改。", "warning");
  subtaskWriteFlow.reset();
  subtaskTaskFlow.reset();
}

function splitSubtaskAtFrame() {
  const frame = subtaskState.frame;
  const segments = subtaskState.segments;
  if (!segments.length) {
    // 还没有分段时，在当前帧切成两段（第 0 帧开始一段、当前帧开始一段）；在第 0 帧时只新建一段。
    segments.push({ start: 0, text: "" });
    if (frame > 0) segments.push({ start: frame, text: "" });
  } else if (segments.some((segment) => segment.start === frame)) {
    subtaskEditorMessage.innerHTML = alertHtml(`第 ${frame} 帧已是某段的起始帧。`, "warning");
    return;
  } else {
    const position = subtaskSegmentAt(frame);
    segments.splice(position + 1, 0, { start: frame, text: segments[position].text });
  }
  markSubtaskDirty();
  renderSubtaskSegments();
  subtaskSegmentsBox.querySelector(`[data-text="${subtaskSegmentAt(frame)}"]`)?.select();
}

async function saveSubtaskEpisode() {
  const episode = subtaskState.episode;
  try {
    const detail = await fetchJson(`${subtaskBase()}/episodes/${episode.episode_index}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments: subtaskState.segments, tasks: subtaskState.tasks }),
    });
    subtaskState.segments = detail.segments.map(({ start, text }) => ({ start, text }));
    subtaskState.episode = { ...subtaskState.episode, ...detail };
    subtaskState.tasks = [...detail.edited_tasks];
    renderSubtaskTasks();
    subtaskState.dirty = false;
    renderSubtaskSegments();
    subtaskEditorMessage.innerHTML = alertHtml("已保存草稿。", "success");
    loadSubtaskEpisodes();
  } catch (error) {
    subtaskEditorMessage.innerHTML = alertHtml(error.message, "error");
  }
}

async function discardSubtaskDrafts(episodeIndices) {
  const scope = episodeIndices ? `episode ${episodeIndices.join(", ")} 的草稿` : "全部草稿";
  if (!window.confirm(`放弃${scope}？将恢复为数据集中已有的标注。`)) return;
  try {
    const result = await postJson(`${subtaskBase()}/discard`, { episode_indices: episodeIndices });
    subtaskMessage.innerHTML = alertHtml(`已放弃 ${result.discarded} 个 episode 的草稿。`, "success");
    subtaskState.dirty = false;
    await loadSubtaskEpisodes();
    if (subtaskState.episode) openSubtaskEpisode(subtaskState.episode.episode_index);
  } catch (error) {
    subtaskMessage.innerHTML = alertHtml(error.message, "error");
  }
}

async function replaceSubtaskText() {
  const find = document.querySelector("#subtask-find").value;
  if (!subtaskDataset.value || !find) {
    subtaskMessage.innerHTML = alertHtml("请填写要查找的文字。", "warning");
    return;
  }
  try {
    const result = await postJson(`${subtaskBase()}/replace`, {
      find,
      replace: document.querySelector("#subtask-replace").value,
      regex: document.querySelector("#subtask-regex").checked,
      scope: document.querySelector("#subtask-replace-scope").value,
    });
    subtaskMessage.innerHTML = alertHtml(`已替换 ${result.changed_episodes.length} 个 episode（存为草稿）。`, "success");
    document.querySelector("#subtask-more").open = false;
    await loadSubtaskEpisodes();
    if (subtaskState.episode && !subtaskState.dirty) openSubtaskEpisode(subtaskState.episode.episode_index);
  } catch (error) {
    subtaskMessage.innerHTML = alertHtml(error.message, "error");
  }
}

const subtaskCsvColumns = ["episode_index", "start_frame", "end_frame", "start_time", "text"];

function csvField(value) {
  const text = String(value ?? "");
  const field = /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  return field;
}

// 解析 CSV（支持引号内的逗号、换行与 "" 转义），首行为表头，返回对象数组。
function parseCsv(text) {
  const records = [];
  let record = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') { field += '"'; index += 1; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { record.push(field); field = ""; }
    else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[index + 1] === "\n") index += 1;
      record.push(field);
      records.push(record);
      record = [];
      field = "";
    } else field += char;
  }
  if (field || record.length) { record.push(field); records.push(record); }
  const [header, ...rows] = records.filter((item) => item.some((value) => value !== ""));
  const names = (header || []).map((name) => name.trim().replace(/^﻿/, ""));
  const objects = rows.map((row) => Object.fromEntries(names.map((name, position) => [name, row[position]])));
  return objects;
}

async function exportSubtasks() {
  if (!subtaskDataset.value) return;
  try {
    const rows = await fetchJson(`${subtaskBase()}/export`);
    const lines = [subtaskCsvColumns.join(","), ...rows.map((row) => subtaskCsvColumns.map((name) => csvField(row[name])).join(","))];
    // 带 BOM，Excel 打开中文不乱码。
    const blob = new Blob(["﻿" + lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${datasetById(subtaskDataset.value)?.name ?? "dataset"}_subtasks.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
    subtaskMessage.innerHTML = alertHtml(`已导出 ${rows.length} 段。`, "success");
  } catch (error) {
    subtaskMessage.innerHTML = alertHtml(error.message, "error");
  }
}

async function importSubtasks(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file || !subtaskDataset.value) return;
  try {
    const text = await file.text();
    const rows = file.name.toLowerCase().endsWith(".json") ? JSON.parse(text) : parseCsv(text);
    const result = await postJson(`${subtaskBase()}/import`, { rows });
    subtaskMessage.innerHTML = alertHtml(`已导入 ${result.imported_episodes.length} 个 episode（存为草稿，文件中的 episode 整体替换）。`, "success");
    document.querySelector("#subtask-more").open = false;
    await loadSubtaskEpisodes();
    if (subtaskState.episode && !subtaskState.dirty) openSubtaskEpisode(subtaskState.episode.episode_index);
  } catch (error) {
    subtaskMessage.innerHTML = alertHtml(`导入失败：${error.message}`, "error");
  }
}

// 生成前要求保存：未保存的页面修改不在草稿中。
function subtaskRequest(result, extra) {
  if (!subtaskDataset.value) return null;
  if (subtaskState.dirty) {
    result.innerHTML = alertHtml("当前 episode 有未保存的修改，请先保存草稿。", "warning");
    return null;
  }
  const body = { dataset_id: Number(subtaskDataset.value), ...extra };
  if (!body.output_name) delete body.output_name;
  return body;
}

function renderSubtaskWritePreview(preview) {
  const counts = preview.counts;
  const html = `
    ${previewStatusHtml(preview, `${counts.modified} 个 episode 有修改，共 ${preview.segments} 段子任务。`)}
    ${statCards([["修改的 episode", counts.modified], ["改了子任务", preview.subtask_edit_episodes], ["改了任务描述", preview.task_edit_episodes], ["子任务段数", preview.segments]])}
    ${outputHtml(preview)}`;
  return html;
}

function renderSubtaskTaskPreview(preview) {
  const examples = preview.examples.map((item) => `episode ${item.episode_index} 第 ${item.start} 帧起：${item.task}`);
  const html = `
    ${previewStatusHtml(preview, `任务描述 ${preview.original_task_count} → ${preview.task_count} 条。`)}
    ${statCards([["任务描述", `${preview.original_task_count} → ${preview.task_count}`], ["有子任务", preview.episode_count - preview.unlabeled_episodes], ["无子任务", preview.unlabeled_episodes], ["含草稿", preview.draft_episodes]])}
    ${examples.length ? detailsHtml("示例", `<div class="text-sm">${listHtml(examples)}</div>`, true) : ""}
    ${outputHtml(preview)}`;
  return html;
}

const subtaskWriteResult = document.querySelector("#subtask-write-result");
const subtaskTaskResult = document.querySelector("#subtask-task-result");
const subtaskWriteOutput = document.querySelector("#subtask-write-output");
const subtaskTaskOutput = document.querySelector("#subtask-task-output");

const subtaskWriteFlow = setupFlow({
  previewButton: document.querySelector("#subtask-write-preview"),
  submitButton: document.querySelector("#subtask-write-submit"),
  result: subtaskWriteResult,
  status: document.querySelector("#subtask-write-task"),
  request: () => subtaskRequest(subtaskWriteResult, { output_name: subtaskWriteOutput.value.trim() }),
  preview: (body) => postJson("/api/subtask/write/preview", body),
  submit: (body) => postJson("/api/subtask/write", body),
  renderPreview: renderSubtaskWritePreview,
  resultText: (result) => `生成 ${baseName(result.output_path)}：写入 ${result.episodes_written} 个 episode，验证 ${result.validation.check_count} 项通过。草稿仍保留，确认后可放弃。`,
  onSucceeded: loadSubtaskDatasets,
});

const subtaskTaskFlow = setupFlow({
  previewButton: document.querySelector("#subtask-task-preview"),
  submitButton: document.querySelector("#subtask-task-submit"),
  result: subtaskTaskResult,
  status: document.querySelector("#subtask-task-task"),
  request: () => subtaskRequest(subtaskTaskResult, {
    template: document.querySelector("#subtask-template").value,
    keep_language: document.querySelector("#subtask-keep-language").checked,
    output_name: subtaskTaskOutput.value.trim(),
  }),
  preview: (body) => postJson("/api/subtask/to-task/preview", body),
  submit: (body) => postJson("/api/subtask/to-task", body),
  renderPreview: renderSubtaskTaskPreview,
  resultText: (result) => `生成 ${baseName(result.output_path)}：${result.converted_episodes} 个 episode 转为逐帧任务，共 ${result.task_count} 条任务描述，验证 ${result.validation.check_count} 项通过。`,
  onSucceeded: loadSubtaskDatasets,
});

function resetSubtaskEditor() {
  Object.assign(subtaskState, { episode: null, segments: [], frame: 0, dirty: false });
  subtaskEditor.hidden = true;
  subtaskEmpty.hidden = false;
  subtaskMessage.innerHTML = "";
  subtaskEpisodes.innerHTML = "";
  subtaskWriteFlow.reset();
  subtaskTaskFlow.reset();
  const dataset = datasetById(subtaskDataset.value);
  subtaskWriteOutput.placeholder = dataset ? `默认 ${dataset.name}_subtask` : "";
  subtaskTaskOutput.placeholder = dataset ? `默认 ${dataset.name}_task` : "";
}

async function loadSubtaskDatasets() {
  try {
    if ((await fillDatasetSelect(subtaskDataset, ["lerobot_v3.0"])) || !subtaskState.episode) resetSubtaskEditor();
    loadSubtaskEpisodes();
  } catch (error) {
    subtaskMessage.innerHTML = alertHtml(error.message, "error");
  }
}

subtaskDataset.addEventListener("change", () => {
  if (subtaskState.dirty && !window.confirm("当前 episode 有未保存的修改，放弃并切换数据集？")) return;
  resetSubtaskEditor();
  loadSubtaskEpisodes();
});
subtaskEpisodes.addEventListener("click", (event) => {
  const button = event.target.closest("[data-episode]");
  if (button) openSubtaskEpisode(Number(button.dataset.episode));
});
subtaskSlider.addEventListener("input", () => setSubtaskFrame(Number(subtaskSlider.value)));
subtaskCamera.addEventListener("change", () => setSubtaskFrame(subtaskState.frame));
subtaskTimeline.addEventListener("click", (event) => {
  const element = event.target.closest("[data-segment]");
  if (element) setSubtaskFrame(subtaskState.segments[Number(element.dataset.segment)].start);
});
subtaskTasksBox.addEventListener("input", (event) => {
  const index = event.target.dataset.task;
  if (index === undefined) return;
  subtaskState.tasks[Number(index)] = event.target.value;
  markSubtaskDirty();
});
subtaskTasksBox.addEventListener("change", renderSubtaskTasks);
subtaskSegmentsBox.addEventListener("input", (event) => {
  const index = event.target.dataset.text;
  if (index === undefined) return;
  subtaskState.segments[Number(index)].text = event.target.value;
  markSubtaskDirty();
  renderSubtaskTimeline();
  setSubtaskFrame(subtaskState.frame);
  openSubtaskSuggest(event.target);
});
subtaskSegmentsBox.addEventListener("focusin", (event) => {
  if (event.target.dataset.text !== undefined) openSubtaskSuggest(event.target);
});
subtaskSegmentsBox.addEventListener("focusout", closeSubtaskSuggest);
// 建议列表的键盘操作：↑↓ 选择，Enter 填入，Esc 关闭。
subtaskSegmentsBox.addEventListener("keydown", (event) => {
  if (subtaskSuggest.hidden || event.target !== subtaskSuggestState.input) return;
  const count = subtaskSuggestState.items.length;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    subtaskSuggestState.active = (subtaskSuggestState.active + (event.key === "ArrowDown" ? 1 : count - 1)) % count;
    renderSubtaskSuggest();
  } else if (event.key === "Enter" && subtaskSuggestState.active >= 0) {
    event.preventDefault();
    pickSubtaskSuggest(subtaskSuggestState.active);
  } else if (event.key === "Escape") {
    closeSubtaskSuggest();
  }
});
// mousedown 时选中，避免输入框先失去焦点把列表关掉。
subtaskSuggest.addEventListener("mousedown", (event) => {
  const item = event.target.closest("[data-suggest]");
  if (!item) return;
  event.preventDefault();
  pickSubtaskSuggest(Number(item.dataset.suggest));
});
window.addEventListener("scroll", closeSubtaskSuggest, true);
subtaskCommon.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-common]");
  if (chip) fillSubtaskAtFrame(subtaskVocabulary()[Number(chip.dataset.common)].text);
});
// 起始帧在输入完成（change）后再排序重绘，避免输入中途跳动。
subtaskSegmentsBox.addEventListener("change", (event) => {
  const index = event.target.dataset.start;
  if (index === undefined) return;
  subtaskState.segments[Number(index)].start = Number(event.target.value);
  subtaskState.segments.sort((first, second) => first.start - second.start);
  markSubtaskDirty();
  renderSubtaskSegments();
});
subtaskSegmentsBox.addEventListener("click", (event) => {
  const jump = event.target.closest("[data-jump]");
  const remove = event.target.closest("[data-remove]");
  if (jump) setSubtaskFrame(subtaskState.segments[Number(jump.dataset.jump)].start);
  if (remove) {
    subtaskState.segments.splice(Number(remove.dataset.remove), 1);
    markSubtaskDirty();
    renderSubtaskSegments();
  }
});
document.addEventListener("keydown", (event) => {
  if (subtaskEditor.hidden || subtaskEditor.closest("[data-panel]").hidden) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) && event.target !== subtaskSlider) return;
  const step = event.shiftKey ? 10 : 1;
  if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
    event.preventDefault();
    setSubtaskFrame(subtaskState.frame + (event.key === "ArrowRight" ? step : -step));
  } else if ((event.key === "s" || event.key === "S") && !event.ctrlKey && !event.metaKey) {
    splitSubtaskAtFrame();
  } else if (/^[1-9]$/.test(event.key) && !event.ctrlKey && !event.metaKey) {
    const item = subtaskVocabulary()[Number(event.key) - 1];
    if (item) fillSubtaskAtFrame(item.text);
  }
});
window.addEventListener("beforeunload", (event) => {
  if (subtaskState.dirty) event.preventDefault();
});
document.querySelectorAll("input[name=subtask-output-mode]").forEach((input) => input.addEventListener("change", () => {
  document.querySelectorAll("[data-subtask-output]").forEach((element) => {
    element.hidden = element.dataset.subtaskOutput !== input.value;
  });
}));
document.querySelector("#subtask-split").addEventListener("click", splitSubtaskAtFrame);
document.querySelector("#subtask-save").addEventListener("click", saveSubtaskEpisode);
document.querySelector("#subtask-discard").addEventListener("click", () => discardSubtaskDrafts([subtaskState.episode.episode_index]));
document.querySelector("#subtask-discard-all").addEventListener("click", () => discardSubtaskDrafts(null));
document.querySelector("#subtask-replace-button").addEventListener("click", replaceSubtaskText);
document.querySelector("#subtask-export").addEventListener("click", exportSubtasks);
document.querySelector("#subtask-import").addEventListener("change", importSubtasks);
subtaskWriteOutput.addEventListener("input", () => subtaskWriteFlow.reset());
["#subtask-template", "#subtask-keep-language", "#subtask-task-output"].forEach((selector) => document.querySelector(selector).addEventListener("input", () => subtaskTaskFlow.reset()));
onPanelShow("subtask", loadSubtaskDatasets);
