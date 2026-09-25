"""验证清单的失败路径：失败项可截断列出，但计数与报错信息按全部失败项。"""
import pytest

from component.common.validation import Checks, require_passed


def test_failed_items_are_limited_but_counted():
    checks = Checks()
    checks.add("episode 数", True)
    for index in range(25):
        checks.add(f"episode_{index:06d} 数值列逐值一致", False)
    summary = checks.summary(failed_limit=20)
    assert not summary["passed"] and len(summary["failed"]) == 20
    assert (summary["failed_count"], summary["check_count"]) == (25, 26)
    with pytest.raises(ValueError, match="验证未通过（25 项）：episode_000000"):
        require_passed(summary)
    require_passed(Checks().summary())
