"""RoboMIND 2.0 天轶任务分批转换：每批下载约 10 GB H5 → 转 LeRobot v3.0 → 验证通过后删 H5 → 全部批次后合并。

本地放不下整个任务的原始 H5 时使用（例如 stack_paper_cups 257 GB）。转换与合并直接调用平台的
convert_job / merge_job，映射用 config/x_humanoid.json，结果与页面上“格式转换”“数据合并”相同，但不登记到数据库。

运行：在仓库根目录 `MANIP_OUTPUT_DIR=<输出目录> uv run python tools/robomind_batch_convert.py`（任务列表在 main 中设置）。
只看任务的文件数与分批：调用 demo_list_and_split()。
中断后重跑即可续上：已完成的批次跳过，已下完的 H5 跳过，未下完的续传。
"""

import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loguru import logger  # noqa: E402

from component.common.dataset_access import DatasetAccess  # noqa: E402
from component.common.task_manager import DemoReporter  # noqa: E402
from component.conversion.data_converter import convert_job  # noqa: E402
from component.merge.data_merger import merge_job  # noqa: E402

MODELSCOPE_API = "https://www.modelscope.cn/api/v1/datasets"
REPO = "X-Humanoid/RoboMIND2.0-Tianyi"
# ModelScope 上的目录名是 tienyi（不是 tianyi）。
TASK_ROOT = "data/tienyi"
MAPPING = REPO_ROOT / "config" / "x_humanoid.json"


def list_task_files(task, repo=REPO):
    """用 ModelScope tree API 列出一个任务的全部 trajectory.hdf5（路径与字节数），按路径排序。"""
    files = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"Revision": "master", "Root": f"{TASK_ROOT}/{task}/success_episodes",
                                        "Recursive": "true", "PageNumber": page, "PageSize": 500})
        with urllib.request.urlopen(f"{MODELSCOPE_API}/{repo}/repo/tree?{query}", timeout=60) as response:
            data = json.load(response)["Data"]
        entries = data["Files"] or []
        files.extend({"path": e["Path"], "size": e["Size"]} for e in entries
                     if e["Type"] == "blob" and e["Path"].endswith("/data/trajectory.hdf5"))
        if not entries or page * 500 >= data["TotalCount"]:
            break
        page += 1
    files.sort(key=lambda f: f["path"])
    return files


def split_batches(files, batch_bytes):
    batches, current, size = [], [], 0
    for entry in files:
        if current and size + entry["size"] > batch_bytes:
            batches.append(current)
            current, size = [], 0
        current.append(entry)
        size += entry["size"]
    if current:
        batches.append(current)
    return batches


def download(entry, raw_root, repo=REPO):
    # 保持 <任务>/success_episodes/<episode>/data/trajectory.hdf5 结构：平台按这个布局发现 episode。
    target = raw_root / entry["path"].removeprefix(f"{TASK_ROOT}/")
    if target.exists() and target.stat().st_size == entry["size"]:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"{MODELSCOPE_API}/{repo}/repo?" + urllib.parse.urlencode({"Revision": "master", "FilePath": entry["path"]})
    partial = target.with_suffix(".part")
    subprocess.run(["curl", "-sSfL", "--retry", "5", "--retry-delay", "5", "-C", "-", "-o", str(partial), url],
                   check=True)
    if partial.stat().st_size != entry["size"]:
        raise IOError(f"{partial} 大小 {partial.stat().st_size}，列表为 {entry['size']}")
    partial.rename(target)
    return target


def convert_batch(task, index, entries, work_dir, output_dir, workers):
    output = output_dir / f"{task}_part{index:02d}"
    if (output / "meta" / "info.json").exists():
        logger.info("Batch {} already converted: {}", index, output)
        return output
    # 不清空 raw_root：重跑时已下完的文件跳过；每批成功后整体删除，残留只属于当前批次。
    raw_root = work_dir / "raw"
    # 单连接约 1 MB/s（2026-09-29 实测），并发下载提高总吞吐。
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for number, target in enumerate(pool.map(lambda e: download(e, raw_root), entries), 1):
            logger.info("Batch {}: downloaded {}/{} {}", index, number, len(entries), target.parent.parent.name)
    source = raw_root / task
    episodes = [e["id"] for e in DatasetAccess().list_episodes(str(source)) if e["status"] != "invalid"]
    params = {"dataset_id": 0, "dataset_path": str(source), "episode_ids": episodes, "mapping_path": str(MAPPING),
              "output_path": str(output), "task": None}
    result = convert_job(params, DemoReporter())
    logger.info("Batch {}: {} episodes, {} frames, skipped {}", index, result["episode_count"], result["frames"],
                result["skipped_episodes"])
    # convert_job 验证未通过时会抛出异常且不发布；走到这里才删除原始 H5。
    shutil.rmtree(raw_root)
    return output


def merge_parts(task, parts, output_dir):
    output = output_dir / f"robomind2_tianyi_{task}"
    if (output / "meta" / "info.json").exists():
        logger.info("Merged dataset exists: {}", output)
        return output
    params = {"sources": [{"id": i, "name": p.name, "path": str(p)} for i, p in enumerate(parts)],
              "output_path": str(output), "duplicates": [], "notes": [f"RoboMIND 2.0 天轶 {task}，分批转换后合并"]}
    result = merge_job(params, DemoReporter())
    logger.info("Merged {}: {} episodes, {} frames", output, result["episode_count"], result["frames"])
    return output


def batch_convert(task, work_dir, output_dir, **kwargs):
    """kwargs：batch_bytes（每批 H5 字节数，默认 10e9）、workers（并发下载数，默认 8）。"""
    work_dir, output_dir = Path(work_dir), Path(output_dir)
    files = list_task_files(task)
    batches = split_batches(files, kwargs.get("batch_bytes", 10e9))
    logger.info("{}: {} episodes, {:.1f} GB, {} batches", task, len(files), sum(f["size"] for f in files) / 1e9,
                len(batches))
    parts_dir = output_dir / "robomind_parts"
    parts = [convert_batch(task, index, entries, work_dir, parts_dir, kwargs.get("workers", 8))
             for index, entries in enumerate(batches)]
    merged = merge_parts(task, parts, output_dir)
    return merged


def demo_list_and_split():
    files = list_task_files("place_cup_in_box_with_right_hand")
    batches = split_batches(files, 10e9)
    sizes = [round(sum(f["size"] for f in batch) / 1e9, 1) for batch in batches]
    logger.info("{} episodes; batch sizes (GB): {}", len(files), sizes)


def main():
    from config.settings import Settings

    # 输出到 MANIP_OUTPUT_DIR（.env 或环境变量）；每批的原始 H5 暂存在其下的 robomind_work/raw，转换后删除。
    settings = Settings()
    tasks = ["place_cup_in_box_with_right_hand"]
    for task in tasks:
        merged = batch_convert(task, settings.output_dir / "robomind_work", settings.output_dir,
                               batch_bytes=10e9, workers=8)
        logger.info("Done: {}", merged)


if __name__ == "__main__":
    main()
