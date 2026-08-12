from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

import main as cli
from src.utils.command_timing import command_timing_session
from src.workflows.chapter_workflow import record_generation_event


class CommandTimingSessionTests(unittest.TestCase):
    def test_new_invocation_excludes_prior_waiting_human_timing(self):
        historical_events = []
        with command_timing_session():
            historical_events.extend(record_generation_event(
                {"chapter_index": 3, "generation_events": historical_events},
                "PROSE_REVIEWED", counter=3, duration_ms=10_000,
            ))

        with command_timing_session():
            historical_events.extend(record_generation_event(
                {"chapter_index": 3, "generation_events": historical_events},
                "PROSE_AGENT_EDITED", counter=1, duration_ms=5_000,
            ))
            historical_events.extend(record_generation_event(
                {"chapter_index": 3, "generation_events": historical_events},
                "PROSE_REVIEWED", counter=4, duration_ms=8_000,
            ))
            output = io.StringIO()
            with redirect_stdout(output):
                cli._print_timing_summary({"generation_events": historical_events})

        rendered = output.getvalue()
        self.assertIn("Agent Prose Edit         5.0s", rendered)
        self.assertIn("Prose Review             8.0s", rendered)
        self.assertIn("Total                    13.0s", rendered)
        self.assertNotIn("18.0s", rendered)
        self.assertEqual(
            [10_000, 5_000, 8_000],
            [event["duration_ms"] for event in historical_events],
        )


if __name__ == "__main__":
    unittest.main()
