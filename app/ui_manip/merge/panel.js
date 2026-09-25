const mergeDatasets = document.querySelector("#merge-datasets");
const mergeOrderList = document.querySelector("#merge-order");
const mergeOutput = document.querySelector("#merge-output");
const mergeResult = document.querySelector("#merge-result");
let mergeNames = {};
// 勾选顺序即合并顺序。
let mergeOrder = [];

const mergeIcons = {
  up: "m18 15-6-6-6 6",
  down: "m6 9 6 6 6-6",
  remove: "M18 6 6 18M6 6l12 12",
};

function mergeIconButton(action, id, label, disabled) {
  const html = `<button type="button" class="ui-btn ui-btn-sm px-1.5" data-action="${action}" data-id="${id}" aria-label="${label}" title="${label}" ${disabled ? "disabled" : ""}>
    <svg class="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${mergeIcons[action]}"></path></svg></button>`;
  return html;
}

function renderMergeChoices(datasets) {
  mergeNames = Object.fromEntries(datasets.map((dataset) => [String(dataset.id), dataset.name]));
  mergeOrder = mergeOrder.filter((id) => id in mergeNames);
  mergeDatasets.innerHTML = datasets.length
    ? datasets.map((dataset) => `
      <label class="flex items-center gap-2 py-1 text-sm" title="${escapeHtml(dataset.path)}">
        <input type="checkbox" value="${dataset.id}" class="ui-check" ${mergeOrder.includes(String(dataset.id)) ? "checked" : ""} />
        <span class="truncate">${escapeHtml(dataset.name)}</span>
      </label>`).join("")
    : `<p class="py-2 text-sm text-gray-500 dark:text-neutral-400">暂无 v3.0 数据集。</p>`;
  renderMergeOrder();
}

function renderMergeOrder() {
  mergeOrderList.innerHTML = mergeOrder.length
    ? mergeOrder.map((id, position) => `
      <li class="flex items-center justify-between gap-2 py-1 text-sm">
        <span class="truncate">${position + 1}. ${escapeHtml(mergeNames[id])}</span>
        <span class="flex shrink-0 gap-1">
          ${mergeIconButton("up", id, `上移 ${mergeNames[id]}`, position === 0)}
          ${mergeIconButton("down", id, `下移 ${mergeNames[id]}`, position === mergeOrder.length - 1)}
          ${mergeIconButton("remove", id, `移除 ${mergeNames[id]}`, false)}
        </span>
      </li>`).join("")
    : `<li class="py-2 text-sm text-gray-500 dark:text-neutral-400">在左侧勾选至少 2 个数据集。</li>`;
  const first = mergeNames[mergeOrder[0]];
  mergeOutput.placeholder = first ? `默认 ${first}_merged` : "";
  mergeFlow.reset();
}

function mergeSourceState(source, position) {
  if (position === 0) return "基准";
  if (!source.differences.length) return `<span class="text-teal-700 dark:text-teal-400">一致</span>`;
  const html = `<span class="text-red-700 dark:text-red-400">${source.differences.length} 项不一致</span><div class="mt-1 text-xs">${listHtml(source.differences)}</div>`;
  return html;
}

function renderMergePreview(preview) {
  const duplicates = preview.duplicates.length
    ? alertHtml(`${preview.duplicates.length} 个 episode 与其他 episode 完全相同，合并后会重复：${preview.duplicates.slice(0, 5).map((item) => `${item.episode[0]} #${item.episode[1]} = ${item.same_as[0]} #${item.same_as[1]}`).join("；")}${preview.duplicates.length > 5 ? " …" : ""}`, "warning")
    : "";
  let start = 0;
  const rows = preview.sources.map((source, position) => {
    const range = `${start}–${start + source.episodes - 1}`;
    start += source.episodes;
    const row = `<tr class="align-top"><td class="ui-td">${position + 1}. ${escapeHtml(source.name)}</td><td class="ui-td">${source.episodes}</td><td class="ui-td">${formatCount(source.frames)}</td><td class="ui-td">${source.fps}</td><td class="ui-td">${range}</td><td class="ui-td">${mergeSourceState(source, position)}</td></tr>`;
    return row;
  });
  const html = `
    ${previewStatusHtml(preview, `合并后 ${formatCount(preview.total_episodes)} 个 episode、${formatCount(preview.total_frames)} 帧。`)}
    ${duplicates}
    ${preview.notes.map((note) => alertHtml(note, "info")).join("")}
    ${statCards([["来源", preview.sources.length], ["episode", formatCount(preview.total_episodes)], ["帧数", formatCount(preview.total_frames)], ["任务描述", preview.total_tasks]])}
    ${tableHtml(["数据集", "episode", "帧", "FPS", "输出编号", "与基准比对"], rows)}
    ${outputHtml(preview)}`;
  return html;
}

function mergeRequest() {
  if (mergeOrder.length < 2) {
    mergeResult.innerHTML = alertHtml("请至少勾选 2 个数据集。", "warning");
    return null;
  }
  const body = { dataset_ids: mergeOrder.map(Number) };
  if (mergeOutput.value.trim()) body.output_name = mergeOutput.value.trim();
  return body;
}

function mergeResultText(result) {
  const text = `生成 ${baseName(result.output_path)}（${formatCount(result.episode_count)} 个 episode、${formatCount(result.frames)} 帧），验证 ${result.validation.check_count} 项通过。`;
  return text;
}

async function loadMergeDatasets() {
  mergeDatasets.innerHTML = loadingHtml();
  try {
    const datasets = (await fetchJson("/api/datasets")).filter((dataset) => dataset.format === "lerobot_v3.0");
    renderMergeChoices(datasets);
  } catch (error) {
    mergeDatasets.innerHTML = alertHtml(error.message, "error");
  }
}

const mergeFlow = setupFlow({
  previewButton: document.querySelector("#merge-preview"),
  submitButton: document.querySelector("#merge-submit"),
  result: mergeResult,
  status: document.querySelector("#merge-task"),
  request: mergeRequest,
  preview: (body) => postJson("/api/merge/preview", body),
  submit: (body) => postJson("/api/merge", body),
  renderPreview: renderMergePreview,
  resultText: mergeResultText,
  onSucceeded: loadMergeDatasets,
});

mergeDatasets.addEventListener("change", (event) => {
  const id = event.target.value;
  mergeOrder = event.target.checked ? [...mergeOrder, id] : mergeOrder.filter((item) => item !== id);
  renderMergeOrder();
});

mergeOrderList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const id = button.dataset.id;
  const from = mergeOrder.indexOf(id);
  if (button.dataset.action === "remove") {
    mergeOrder = mergeOrder.filter((item) => item !== id);
    mergeDatasets.querySelector(`input[value="${id}"]`).checked = false;
  } else {
    const to = from + (button.dataset.action === "up" ? -1 : 1);
    [mergeOrder[from], mergeOrder[to]] = [mergeOrder[to], mergeOrder[from]];
  }
  renderMergeOrder();
  // 列表重绘后把键盘焦点放回同一行（移到首尾时该按钮禁用，改为同行其他按钮）。
  const row = [...mergeOrderList.querySelectorAll(`[data-id="${id}"]`)];
  (row.find((item) => item.dataset.action === button.dataset.action && !item.disabled) ?? row.find((item) => !item.disabled))?.focus();
});

mergeOutput.addEventListener("input", () => mergeFlow.reset());
onPanelShow("merge", loadMergeDatasets);
