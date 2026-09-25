const splitDataset = document.querySelector("#split-dataset");
const splitDatasetInfo = document.querySelector("#split-dataset-info");
const subsetChoices = document.querySelector("#subset-choices");
const subsetOutput = document.querySelector("#subset-output");
let subsetOptions = null;

function subsetMode() {
  const mode = document.querySelector("input[name=subset-mode]:checked").value;
  return mode;
}

// 勾选列表：每项显示取值与涉及的 episode 数、帧数；带“全选 / 清空”。
function checkList(items) {
  const rows = items.map((item) => `
    <label class="flex items-start gap-2 py-1 text-sm">
      <input type="checkbox" value="${escapeHtml(item.value)}" class="ui-check mt-0.5" />
      <span class="min-w-0 break-words">${escapeHtml(item.label === "" ? "（空）" : item.label)}
        <span class="text-gray-500 dark:text-neutral-400">· ${item.episodes} 个 episode、${formatCount(item.frames)} 帧</span></span>
    </label>`);
  const html = `
    <div class="rounded-lg border border-gray-200 dark:border-neutral-700">
      <div class="flex gap-2 border-b border-gray-200 px-3 py-1.5 dark:border-neutral-700">
        <button type="button" class="ui-link text-xs" data-check-all="true">全选</button>
        <button type="button" class="ui-link text-xs" data-check-all="false">清空</button>
      </div>
      <div class="max-h-72 overflow-y-auto px-3 py-1.5">${rows.join("")}</div>
    </div>`;
  return html;
}

function renderValueChoices() {
  const column = document.querySelector("#subset-column").value;
  const items = subsetOptions.columns[column].map((item) => ({ value: item.value, label: item.value, episodes: item.episodes, frames: item.frames }));
  document.querySelector("#subset-values").innerHTML = checkList(items);
  subsetFlow.reset();
}

function renderSubsetChoices() {
  subsetFlow.reset();
  if (!subsetOptions) {
    subsetChoices.innerHTML = "";
    return;
  }
  const mode = subsetMode();
  if (mode === "episodes") {
    const last = subsetOptions.episode_count - 1;
    subsetChoices.innerHTML = `
      <div class="max-w-md">
        <label for="subset-episodes" class="ui-label">编号</label>
        <input id="subset-episodes" class="ui-input" autocomplete="off" placeholder="如 0-99, 120, 150-160" />
        <p class="ui-hint mt-1.5">共 ${subsetOptions.episode_count} 个，编号 0–${last}；范围包含两端。</p>
      </div>`;
    return;
  }
  if (mode === "tasks") {
    const items = subsetOptions.tasks.map((task) => ({ value: task.task_index, label: task.task, episodes: task.episodes, frames: task.frames }));
    subsetChoices.innerHTML = checkList(items);
    return;
  }
  const columns = Object.keys(subsetOptions.columns);
  if (!columns.length) {
    subsetChoices.innerHTML = alertHtml("该数据集没有标签列。", "info", ` <button type="button" class="ui-link" data-open-panel="tag">去添加标签</button>`);
    return;
  }
  const options = columns.map((column) => `<option value="${escapeHtml(column)}">${escapeHtml(column)}</option>`).join("");
  subsetChoices.innerHTML = `
    <div class="max-w-xs">
      <label for="subset-column" class="ui-label">标签列</label>
      <select id="subset-column" class="ui-input">${options}</select>
    </div>
    <div id="subset-values"></div>`;
  document.querySelector("#subset-column").addEventListener("change", renderValueChoices);
  renderValueChoices();
}

async function loadSubsetOptions() {
  subsetOptions = null;
  const dataset = datasetById(splitDataset.value);
  subsetOutput.placeholder = dataset ? `默认 ${dataset.name}_subset` : "";
  if (dataset) {
    subsetChoices.innerHTML = loadingHtml();
    try {
      subsetOptions = await fetchJson(`/api/split/options?dataset_id=${dataset.id}`);
      splitDatasetInfo.textContent = `${subsetOptions.episode_count} 个 episode · ${formatCount(subsetOptions.total_frames)} 帧`;
    } catch (error) {
      subsetChoices.innerHTML = alertHtml(error.message, "error");
      return;
    }
  }
  renderSubsetChoices();
}

function subsetRequest() {
  if (!subsetOptions) return null;
  const mode = subsetMode();
  const body = { dataset_id: Number(splitDataset.value), mode };
  const checked = [...subsetChoices.querySelectorAll("input[type=checkbox]:checked")].map((input) => input.value);
  if (mode === "episodes") body.episodes = document.querySelector("#subset-episodes").value;
  if (mode === "tasks") body.tasks = checked.map(Number);
  if (mode === "values") {
    const column = document.querySelector("#subset-column");
    if (!column) return null;
    body.column = column.value;
    body.values = checked;
  }
  if (subsetOutput.value.trim()) body.output_name = subsetOutput.value.trim();
  return body;
}

function renderSubsetPreview(preview) {
  const summary = preview.summary;
  const shown = preview.episodes.slice(0, 200).join(", ") + (preview.episodes.length > 200 ? ` …（共 ${preview.episodes.length} 个）` : "");
  const mapping = `<p>按以下顺序重新编号为 0–${Math.max(summary.episode_count - 1, 0)}：</p><p class="mt-1 font-mono text-xs break-all">${escapeHtml(shown)}</p>`;
  const html = `
    ${previewStatusHtml(preview, `选中 ${summary.episode_count} 个 episode、${formatCount(summary.frames)} 帧。`)}
    ${preview.notes.map((note) => alertHtml(note, "warning")).join("")}
    ${statCards([["选中 episode", summary.episode_count], ["帧数", formatCount(summary.frames)], ["时长", `${summary.seconds} 秒`], ["未选", summary.unselected_count]])}
    ${preview.episodes.length ? detailsHtml("选中的源 episode", mapping) : ""}
    ${outputHtml(preview)}`;
  return html;
}

function subsetResultText(result) {
  const text = `生成 ${baseName(result.output_path)}（${result.episode_count} 个 episode、${formatCount(result.frames)} 帧），验证 ${result.validation.check_count} 项通过。`;
  return text;
}

async function loadSplitDatasets() {
  try {
    if ((await fillDatasetSelect(splitDataset, ["lerobot_v3.0"])) || !subsetOptions) loadSubsetOptions();
  } catch (error) {
    subsetChoices.innerHTML = alertHtml(error.message, "error");
  }
}

const subsetFlow = setupFlow({
  previewButton: document.querySelector("#subset-preview"),
  submitButton: document.querySelector("#subset-submit"),
  result: document.querySelector("#subset-result"),
  status: document.querySelector("#subset-task"),
  request: subsetRequest,
  preview: (body) => postJson("/api/split/preview", body),
  submit: (body) => postJson("/api/split", body),
  renderPreview: renderSubsetPreview,
  resultText: subsetResultText,
  onSucceeded: loadSplitDatasets,
});

splitDataset.addEventListener("change", loadSubsetOptions);
document.querySelectorAll("input[name=subset-mode]").forEach((input) => input.addEventListener("change", renderSubsetChoices));
subsetChoices.addEventListener("input", () => subsetFlow.reset());
subsetChoices.addEventListener("click", (event) => {
  const button = event.target.closest("[data-check-all]");
  if (!button) return;
  subsetChoices.querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.checked = button.dataset.checkAll === "true";
  });
  subsetFlow.reset();
});
subsetOutput.addEventListener("input", () => subsetFlow.reset());
onPanelShow("split", loadSplitDatasets);
