"""Pytest 兼容层。

tests/ 下的测试类没有继承 unittest.TestCase，却使用了
self.assertIsNotNone 等 unittest 断言。这里在收集阶段把
unittest.TestCase 的断言方法注入到缺少它们的测试类上，
不修改任何测试文件。
"""

import unittest


def pytest_collection_modifyitems(items):
    patched = set()
    for item in items:
        cls = getattr(item, "cls", None)
        if cls is None or cls in patched or issubclass(cls, unittest.TestCase):
            continue
        patched.add(cls)
        if hasattr(cls, "assertIsNotNone"):
            continue
        for name, value in vars(unittest.TestCase).items():
            if name.startswith("__"):
                continue
            if not hasattr(cls, name):
                try:
                    setattr(cls, name, value)
                except (AttributeError, TypeError):
                    pass
