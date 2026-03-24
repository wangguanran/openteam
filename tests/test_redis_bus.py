import os
import sys
import unittest
from unittest import mock


def _add_template_app_to_syspath():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import redis_bus  # noqa: E402


class RedisBusTests(unittest.TestCase):
    def test_describe_not_configured(self):
        with mock.patch.dict(os.environ, {"OPENTEAM_REDIS_URL": ""}):
            out = redis_bus.describe()
        self.assertFalse(out.get("configured"))
        self.assertFalse(out.get("available"))
        self.assertEqual(out.get("reason"), "not_configured")

    def test_operations_graceful_when_not_configured(self):
        with mock.patch.dict(os.environ, {"OPENTEAM_REDIS_URL": ""}):
            pub = redis_bus.publish_event("openteam.events", {"event_type": "TASK_NEW"})
            enq = redis_bus.enqueue("openteam.queue", {"task_id": "T-1"})
            deq = redis_bus.dequeue("openteam.queue", timeout=0)
            cset = redis_bus.cache_set("k1", {"v": 1}, ttl=5)
            cget = redis_bus.cache_get("k1")
        self.assertTrue(pub.get("ok"))
        self.assertTrue(pub.get("skipped"))
        self.assertTrue(enq.get("ok"))
        self.assertTrue(enq.get("skipped"))
        self.assertTrue(deq.get("ok"))
        self.assertTrue(deq.get("skipped"))
        self.assertIsNone(deq.get("item"))
        self.assertTrue(cset.get("ok"))
        self.assertTrue(cset.get("skipped"))
        self.assertTrue(cget.get("ok"))
        self.assertTrue(cget.get("skipped"))
        self.assertIsNone(cget.get("value"))

    def test_missing_dependency_is_graceful(self):
        with mock.patch.dict(os.environ, {"OPENTEAM_REDIS_URL": "redis://127.0.0.1:6379/0"}):
            with mock.patch.object(redis_bus, "_import_redis_module", side_effect=ImportError("redis not installed")):
                out = redis_bus.describe()
                pub = redis_bus.publish_event("openteam.events", {"event_type": "RUN_STARTED"})
        self.assertTrue(out.get("configured"))
        self.assertFalse(out.get("dependency_ok"))
        self.assertFalse(out.get("available"))
        self.assertEqual(out.get("reason"), "dependency_missing")
        self.assertTrue(pub.get("ok"))
        self.assertTrue(pub.get("skipped"))
        self.assertEqual(pub.get("reason"), "dependency_missing")


if __name__ == "__main__":
    unittest.main()

