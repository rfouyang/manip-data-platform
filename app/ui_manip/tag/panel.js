const tagDataset = document.querySelector("#tag-dataset");
const tagDatasetInfo = document.querySelector("#tag-dataset-info");
const tagTasks = document.querySelector("#tag-tasks");
const tagColumn = document.querySelector("#tag-column");
const tagPattern = document.querySelector("#tag-pattern");
const tagDefault = document.querySelector("#tag-default");
const tagValue = document.querySelector("#tag-value");
const tagOutput = document.querySelector("#tag-output");
const tagResult = document.querySelector("#tag-result");

// 空字符串（未匹配且未填默认值）显示为“（空）”。
function tagValueText(value) {
  const text = value === "" ? "（空）" : value;
  return text;
}

function tagMode() {
  const mode = document.querySelector("input[name=tag-mode]:checked").value;
  return mode;
}

function showTagMode() {
  const mode = tagMode();
  document.querySelectorAll("[data-tag-mode]").forEach((element) => {
    element.hidden = element.dataset.tagMode !== mode;
  });
  tagFlow.reset();
}

function updateTagOutputPlaceholder() {
  const dataset = datasetById(tagDataset.value);
  const column = tagColumn.value.trim() || "tag";
  tagOutput.placeholder = dataset ? `默认 ${dataset.name}_${column}` : "";
}

function tagRequest() {
  const mode = tagMode();
  const missing = mode === "regex" ? !tagPattern.value : !tagValue.value.trim();
  if (!tagDataset.value || missing) {
    tagResult.innerHTML = alertHtml(mode === "regex" ? "请填写正则表达式。" : "请填写标签值。", "warning");
    return null;
  }
  const body = { dataset_id: Number(tagDataset.value), mode, column: tagColumn.value.trim() || "tag" };
  if (mode === "constant") body.value = tagValue.value.trim();
  if (mode === "regex") body.pattern = tagPattern.value;
  if (mode === "regex" && tagDefault.value.trim()) body.default = tagDefault.value.trim();
  if (tagOutput.value.trim()) body.output_name = tagOutput.value.trim();
  return body;
}

// 写正则前先看到该数据集的全部任务描述（可展开）。
async function loadTagTasks() {
  updateTagOutputPlaceholder();
  tagFlow.reset();
  tagTasks.innerHTML = "";
  if (!tagDataset.value) return;
  tagTasks.innerHTML = loadingHtml();
  try {
    const preview = await fetchJson(`/api/datasets/${tagDataset.value}/preview?rows=1`);
    tagDatasetInfo.textContent = `${preview.summary.episodes} 个 episode · ${formatCount(preview.summary.frames)} 帧 · ${preview.tasks.length} 条任务描述`;
    const rows = preview.tasks.map((task) => `<tr><td class="ui-td select-all">${escapeHtml(task.task)}</td><td class="ui-td">${task.episodes}</td><td class="ui-td">${formatCount(task.frames)}</td></tr>`);
    tagTasks.innerHTML = detailsHtml(`查看任务描述（${preview.tasks.length}）`, tableHtml(["任务描述", "episode", "帧"], rows, "max-h-72"));
  } catch (error) {
    tagTasks.innerHTML = alertHtml(error.message, "error");
  }
}

function renderTagPreview(preview) {
  const passText = preview.mode === "constant"
    ? `全部 ${formatCount(preview.total_frames)} 帧的 ${preview.column} 为“${preview.value}”。`
    : `${preview.values.length} 个不同的值，${preview.unmatched_count} 条任务描述未匹配。`;
  const valueRows = preview.values.map((item) => `<tr><td class="ui-td font-mono">${escapeHtml(tagValueText(item.value))}</td><td class="ui-td">${item.episodes}</td><td class="ui-td">${formatCount(item.frames)}</td></tr>`);
  const taskRows = preview.tasks.map((task) => {
    const value = task.matched ? escapeHtml(task.value) : `<span class="text-yellow-700 dark:text-yellow-400">未匹配 → ${escapeHtml(tagValueText(task.value))}</span>`;
    const row = `<tr><td class="ui-td">${escapeHtml(task.task)}</td><td class="ui-td font-mono">${value}</td><td class="ui-td">${task.episodes}</td></tr>`;
    return row;
  });
  const html = `
    ${previewStatusHtml(preview, passText)}
    ${statCards([["新列", preview.column], ["不同取值", preview.values.length], ["未匹配", preview.unmatched_count], ["帧数", formatCount(preview.total_frames)]])}
    ${tableHtml(["取值", "episode", "帧"], valueRows, "max-h-60")}
    ${preview.mode === "regex" ? detailsHtml("每条任务描述的取值", tableHtml(["任务描述", "取值", "episode"], taskRows, "max-h-80")) : ""}
    ${outputHtml(preview)}`;
  return html;
}

function tagResultText(result) {
  const values = Object.entries(result.values).map(([value, count]) => `${tagValueText(value)} ${formatCount(count)} 帧`).join("、");
  const text = `生成 ${baseName(result.output_path)}，${result.column}：${values}；验证 ${result.validation.check_count} 项通过。`;
  return text;
}

async function loadTagDatasets() {
  try {
    if ((await fillDatasetSelect(tagDataset, ["lerobot_v3.0"])) || !tagTasks.innerHTML) loadTagTasks();
  } catch (error) {
    tagTasks.innerHTML = alertHtml(error.message, "error");
  }
}

const tagFlow = setupFlow({
  previewButton: document.querySelector("#tag-preview"),
  submitButton: document.querySelector("#tag-submit"),
  result: tagResult,
  status: document.querySelector("#tag-task"),
  request: tagRequest,
  preview: (body) => postJson("/api/tag/preview", body),
  submit: (body) => postJson("/api/tag", body),
  renderPreview: renderTagPreview,
  resultText: tagResultText,
  onSucceeded: loadTagDatasets,
});

[tagColumn, tagPattern, tagDefault, tagValue, tagOutput].forEach((input) => input.addEventListener("input", () => tagFlow.reset()));
tagColumn.addEventListener("input", updateTagOutputPlaceholder);
document.querySelectorAll("input[name=tag-mode]").forEach((input) => input.addEventListener("change", showTagMode));
tagDataset.addEventListener("change", loadTagTasks);
onPanelShow("tag", loadTagDatasets);
