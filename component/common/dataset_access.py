import json
from pathlib import Path

import h5py
from loguru import logger

from util import lerobot_helper
from util.h5_helper import EPISODE_FILE, H5Episode, find_episodes, is_lfs_pointer


H5_DESCRIPTION_FILE = "zh_description.txt"
LEROBOT_V3 = "lerobot_v3.0"
LEROBOT_V21 = "lerobot_v2.1"
LEROBOT_FORMATS = {"v3.0": LEROBOT_V3, "v2.1": LEROBOT_V21}


class DatasetAccess:
    """格式识别与统一读取：H5、LeRobot v3.0 与 v2.1。

    H5 的 episode_id 为 episode 目录相对数据集根目录的路径，例如 success_episodes/0106_102314；
    LeRobot 的 episode_id 为 episode_000000 形式，对应 episode_index。
    """

    def detect_format(self, root):
        root = Path(root)
        info_file = root / "meta" / "info.json"
        if info_file.exists():
            info = json.loads(info_file.read_text(encoding="utf-8"))
            version = info.get("codebase_version")
            if version not in LEROBOT_FORMATS:
                raise ValueError(f"识别为 LeRobot {version} 数据集，当前只支持 v3.0 与 v2.1")
            dataset_format = LEROBOT_FORMATS[version]
            return dataset_format
        first_episode = next(root.rglob(EPISODE_FILE), None)
        if first_episode is None:
            raise ValueError(f"未找到 {EPISODE_FILE}，无法识别数据集格式: {root}")
        dataset_format = "h5"
        return dataset_format

    def read_description(self, root):
        description_file = Path(root) / H5_DESCRIPTION_FILE
        description = description_file.read_text(encoding="utf-8").strip() if description_file.exists() else None
        return description

    def episode_path(self, root, episode_id):
        """把 episode_id 映射为文件路径；拒绝跳出数据集根目录的路径。"""
        root = Path(root).resolve()
        path = (root / episode_id / "data" / EPISODE_FILE).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise LookupError(f"episode 不存在: {episode_id}")
        return path

    def episode_summary(self, root, path):
        episode_id = path.parent.parent.relative_to(root).as_posix()
        summary = {
            "id": episode_id,
            "status": "ok",
            "length": None,
            "task": None,
            "collection_time": None,
            "issues": [],
            "error": None,
        }
        if not h5py.is_hdf5(path):
            size = path.stat().st_size
            reason = "未下载的 Git LFS 指针文件" if is_lfs_pointer(path) else "不是 HDF5 文件"
            summary["status"] = "invalid"
            summary["error"] = f"{reason}（{size} 字节）"
            return summary

        with H5Episode(path) as episode:
            metadata = episode.metadata()
            # 空 pose 字段在所有样本中都存在，属于已知结构，不计为问题。
            issues = [issue for issue in episode.check() if issue["type"] != "empty"]
            summary["length"] = episode.length
            summary["task"] = metadata.get("language_instruction")
            summary["collection_time"] = metadata.get("collection_time")
            summary["issues"] = issues
        if issues:
            summary["status"] = "issues"
        return summary

    def list_episodes(self, root):
        """读取每个 episode 的元信息和字段形状，不解码图像。"""
        if self.detect_format(root) != "h5":
            summaries = lerobot_helper.list_episodes(root)
            return summaries
        root = Path(root).resolve()
        summaries = []
        for path in find_episodes(root):
            summaries.append(self.episode_summary(root, path))
        return summaries

    def open_episode(self, root, episode_id):
        """返回已打开的 H5Episode 或 LeRobotEpisode，调用方负责关闭（建议用 with）。"""
        if self.detect_format(root) != "h5":
            episode = lerobot_helper.LeRobotEpisode(root, lerobot_helper.episode_index_of(episode_id))
            return episode
        path = self.episode_path(root, episode_id)
        if not h5py.is_hdf5(path):
            raise ValueError(f"episode 文件不可读取: {episode_id}")
        episode = H5Episode(path)
        return episode

    def read_episode(self, root, episode_id):
        with self.open_episode(root, episode_id) as episode:
            info = episode.info()
        info["id"] = episode_id
        return info


REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_ROOT = REPO_ROOT / "data" / "place_red_pepper_on_red_tray"


def demo_dataset_access():
    access = DatasetAccess()
    dataset_format = access.detect_format(SAMPLE_ROOT)
    episodes = access.list_episodes(SAMPLE_ROOT)
    invalid = [episode for episode in episodes if episode["status"] != "ok"]
    first = access.read_episode(SAMPLE_ROOT, episodes[0]["id"])
    logger.info("Format={}, description={}", dataset_format, access.read_description(SAMPLE_ROOT))
    logger.info("Episodes={}, not ok={}", len(episodes), invalid)
    logger.info("First episode {}: {} frames, cameras={}", first["id"], first["length"], list(first["cameras"]))
    try:
        access.read_episode(SAMPLE_ROOT, "../../config")
        raise AssertionError("path outside dataset root should be rejected")
    except LookupError as error:
        logger.info("Rejected outside path: {}", error)


def main():
    demo_dataset_access()


if __name__ == "__main__":
    main()
