import unittest

from breakout.render import watch_lines


def run(**extra):
    base = dict(id="b1", task="Review the README", status="working", phase="discuss",
                participants=[dict(provider="claude", model="m1", name="Architect", role="author"),
                              dict(provider="codex", model="m2", name="Sol", role="reviewer")],
                messages=[], attempts=[], deliveries=[])
    return dict(base, **extra)


class WatchTest(unittest.TestCase):
    def test_reports_each_event_once(self):
        seen = set()
        first = watch_lines(run(attempts=[dict(id="a1", participant=1, status="running", created=0)]), seen)
        self.assertIn("  Sol · codex m2 · reviewer", first)
        self.assertTrue(any("Sol is thinking" in line for line in first))
        message = dict(id="m1", author="codex", name="Sol", created=0,
                       content=dict(message="Looks good. More detail follows.", issues=[dict(id="I1", status="agreed")]))
        later = watch_lines(run(status="ready for review", messages=[message],
                                deliveries=[dict(revision=1, provider="claude", status="queued")]), seen)
        self.assertEqual(len([l for l in later if "Sol: Looks good." in l]), 1)
        self.assertTrue(any("I1 agreed" in l for l in later))
        self.assertTrue(any("ready for review" in l for l in later))
        self.assertTrue(any("result r1 queued for the claude session" in l for l in later))
        self.assertEqual(watch_lines(run(status="ready for review", messages=[message],
                                         deliveries=[dict(revision=1, provider="claude", status="queued")]), seen), [])


if __name__ == "__main__":
    unittest.main()
