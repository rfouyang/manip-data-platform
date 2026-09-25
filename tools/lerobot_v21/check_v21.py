"""在隔离环境（lerobot 0.3.3，最后一个 v2.1 版本）中用官方 LeRobotDataset 读取 v2.1 数据集，输出 JSON 摘要。

只用于验证平台写出的 v2.1 能被官方 v2.1 代码读取；平台服务本身不依赖此环境。
运行：在 tools/lerobot_v21 下 `uv run python check_v21.py`，或由测试调用 check_dataset。
"""
import json
import os
from pathlib import Path

# 本地数据集不需要访问 Hub；0.3.3 在本地元数据缺失时会去 Hub 查询版本。
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np  # noqa: E402
from lerobot.datasets.lerobot_dataset import LeRobotDataset  # noqa: E402


def create_sample(root, episode_lengths=(12, 9)):
    """用官方 0.3.3 代码创建一个小型 v2.1 数据集（64×64 视频 + 3 维 state/action），作为真正的 v2.1 输入样本。"""
    features = {
        "observation.state": {"dtype": "float32", "shape": (3,), "names": ["a", "b", "c"]},
        "action": {"dtype": "float32", "shape": (3,), "names": ["a", "b", "c"]},
        "observation.images.cam": {"dtype": "video", "shape": (64, 64, 3), "names": ["height", "width", "channels"]},
    }
    dataset = LeRobotDataset.create(f"local/{Path(root).name}", 30, features, root=root, robot_type="demo", use_videos=True)
    for episode_index, length in enumerate(episode_lengths):
        for frame in range(length):
            value = episode_index * 100 + frame
            image = np.zeros((64, 64, 3), np.uint8)
            image[:, : 4 * (frame + 1)] = 40 + 10 * frame
            dataset.add_frame({
                "observation.state": np.array([value, -value, 0.5], np.float32),
                "action": np.array([value + 1, -value - 1, 0.25], np.float32),
                "observation.images.cam": image,
            }, task=f"task {episode_index % 2}")
        dataset.save_episode()
    summary = {"root": str(root), "episodes": dataset.meta.total_episodes, "frames": dataset.meta.total_frames}
    return summary


def check_dataset(root):
    dataset = LeRobotDataset(f"local/{Path(root).name}", root=root, video_backend="pyav")
    samples = []
    for episode_index, episode in dataset.meta.episodes.items():
        start = sum(dataset.meta.episodes[index]["length"] for index in range(episode_index))
        for offset in sorted({0, episode["length"] // 2, episode["length"] - 1}):
            item = dataset[start + offset]
            sample = {
                "index": start + offset,
                "episode_index": item["episode_index"].item(),
                "frame_index": item["frame_index"].item(),
                "task": item["task"],
                "state": item["observation.state"].tolist() if "observation.state" in item else None,
                "image_means": {key: float(item[key].mean()) for key in dataset.meta.camera_keys},
                "shapes": {key: list(value.shape) for key, value in item.items() if hasattr(value, "shape") and value.ndim >= 1},
            }
            samples.append(sample)
    summary = {
        "codebase_version": dataset.meta.info["codebase_version"],
        "total_episodes": dataset.meta.total_episodes,
        "num_frames": dataset.num_frames,
        "fps": dataset.fps,
        "camera_keys": dataset.meta.camera_keys,
        "stats_keys": sorted(dataset.meta.stats),
        "samples": samples,
    }
    return summary


def main():
    root = Path(__file__).resolve().parents[2] / "output" / "demo" / "data_converter" / "converted" / "sample_2_episodes_lerobot_v21"
    summary = check_dataset(root)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
