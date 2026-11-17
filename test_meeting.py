import unittest

from meeting import dispatch


class MeetingToolsTest(unittest.TestCase):
    def test_read_only_observed_segments(self):
        seen = {"01": "第一段"}
        self.assertEqual(dispatch("read_segment", {"segment": "01"}, seen, {}, "01"), "第一段")
        with self.assertRaises(ValueError):
            dispatch("read_segment", {"segment": "03B"}, seen, {}, "01")

    def test_deferred_task_keeps_origin_and_owner(self):
        records = {}
        seen = {"01": "准备", "03B": "暂缓"}
        def write(segment, text, status, **extra):
            return dispatch("write_record", dict(kind="待办", text=text, status=status, **extra),
                            seen, records, segment)["id"]
        original = write("01", "准备上线", "有效", owner="B")
        self.assertEqual(write("03B", "准备上线", "待确认", id=original), original)
        check = write("03B", "核对结果", "有效", owner="B")
        self.assertNotEqual(check, original)
        tasks = dispatch("list_tasks", {}, seen, records, "03B")
        self.assertEqual([v["source"] for v in tasks[original]["versions"]], ["01", "03B"])
        self.assertEqual(tasks[original]["versions"][-1]["owner"], "B")
        self.assertEqual(tasks[check]["versions"][-1]["due"], "")


if __name__ == "__main__":
    unittest.main()
