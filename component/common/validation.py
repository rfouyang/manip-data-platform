"""输出验证清单：逐项记录检查结果并汇总，验证未通过时任务失败。转换、tag、拆分、合并、子任务共用。"""
from pathlib import Path

import numpy as np
from loguru import logger


STANDARD_DIRS = {"data", "meta", "videos"}


class Checks:
    def __init__(self):
        self.items = []

    def add(self, name, passed, detail=""):
        self.items.append({"name": name, "passed": bool(passed), "detail": detail})

    def standard_dirs(self, root):
        entries = sorted(path.name for path in Path(root).iterdir())
        self.add("输出只含 LeRobot 标准目录", set(entries) <= STANDARD_DIRS, str(entries))

    def sample_frames(self, label, episode, reference):
        """每路相机抽查首、中、末帧，RGB 与深度逐像素相等（视频为流拷贝或整文件复制）。"""
        picks = sorted({0, episode.length // 2, episode.length - 1})
        for camera in episode.cameras:
            has_depth = episode.depth_key(camera) is not None
            for pick in picks:
                self.add(f"{label} {camera} 第 {pick} 帧 RGB 一致", np.array_equal(episode.read_color(camera, pick), reference.read_color(camera, pick)))
                if has_depth:
                    self.add(f"{label} {camera} 第 {pick} 帧深度一致", np.array_equal(episode.read_depth(camera, pick), reference.read_depth(camera, pick)))

    def summary(self, **kwargs):
        """failed_limit 限制结果中列出的失败项数（逐 episode 检查的数据集可能有上千项）。"""
        failed = [item for item in self.items if not item["passed"]]
        limit = kwargs.get("failed_limit")
        summary = {"passed": not failed, "failed": failed[:limit] if limit else failed, "failed_count": len(failed), "check_count": len(self.items)}
        return summary


def require_passed(validation, label="输出"):
    if not validation["passed"]:
        names = "；".join(item["name"] for item in validation["failed"][:5])
        raise ValueError(f"{label}验证未通过（{validation['failed_count']} 项）：{names}")


def demo_checks():
    checks = Checks()
    checks.add("episode 数", 3 == 3)
    checks.add("帧数", 10 == 9, "10 / 9")
    summary = checks.summary(failed_limit=5)
    assert not summary["passed"] and summary["check_count"] == 2 and [item["name"] for item in summary["failed"]] == ["帧数"]
    try:
        require_passed(summary)
        raise AssertionError("failed validation should raise")
    except ValueError as error:
        logger.info("Rejected: {}", error)


def main():
    demo_checks()


if __name__ == "__main__":
    main()
