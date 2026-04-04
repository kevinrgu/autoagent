"""Tests for preflight policy gate."""

from preflight import check_diff


def test_reject_fixed_modification():
    diff = """\
diff --git a/adapter.py b/adapter.py
--- a/adapter.py
+++ b/adapter.py
@@ -1,3 +1,4 @@
+# modified
 import autoagent
"""
    result = check_diff(diff)
    assert result.rejected is True
    assert "adapter.py" in result.reason


def test_reject_forbidden_import():
    diff = """\
diff --git a/agent.py b/agent.py
--- a/agent.py
+++ b/agent.py
@@ -1,3 +1,4 @@
+import importlib
 async def run_task(task):
     pass
"""
    result = check_diff(diff)
    assert result.rejected is True
    assert result.reason != ""


def test_allow_clean_change():
    diff = """\
diff --git a/agent.py b/agent.py
--- a/agent.py
+++ b/agent.py
@@ -1,3 +1,5 @@
+import os
+
 async def run_task(task):
-    pass
+    return {"score": 1.0}
"""
    result = check_diff(diff)
    assert result.rejected is False


def test_reject_sys_modules():
    diff = """\
diff --git a/agent.py b/agent.py
--- a/agent.py
+++ b/agent.py
@@ -1,3 +1,4 @@
+sys.modules["os"] = None
 async def run_task(task):
     pass
"""
    result = check_diff(diff)
    assert result.rejected is True
    assert result.reason != ""
