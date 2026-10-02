"""Pytest 兼容层: 骨架测试类未继承 unittest.TestCase,
这里为其补齐 assertIsNotNone, 不修改任何测试文件。"""
import unittest


def _install_assert_shims():
    try:
        from tests.test_raid import TestRaidSystem
    except Exception:
        return
    for name in dir(unittest.TestCase):
        if name.startswith("assert") and not hasattr(TestRaidSystem, name):
            setattr(TestRaidSystem, name, getattr(unittest.TestCase, name))


_install_assert_shims()
