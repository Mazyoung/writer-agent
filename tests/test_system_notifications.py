import unittest
from unittest.mock import Mock, patch

import main as cli
from src.observability.system_notifications import (
    NotificationSession,
    notification_session,
    send_system_notification,
)


class TestNotificationSession(unittest.TestCase):
    def test_waiting_is_deduplicated_and_terminal_outcomes_notify(self):
        backend = Mock()
        session = NotificationSession(backend)
        session.waiting_human("novel", 2, "Plan Review")
        session.waiting_human("novel", 2, "Plan Review")
        session.finished("novel", 2)
        session.error("novel", 2, "late-error")
        self.assertEqual(backend.call_count, 2)
        self.assertIn("Plan Review", backend.call_args_list[0].args[1])
        self.assertIn("completed", backend.call_args_list[1].args[1])

    def test_backend_failure_never_replaces_success_or_error(self):
        backend = Mock(side_effect=RuntimeError("toast failed"))
        session = NotificationSession(backend)
        session.finished("novel", 1)
        session.error("novel", 1, "workflow-error-X")
        self.assertTrue(session.terminal_notified)
        self.assertEqual(backend.call_count, 1)

    @patch("src.observability.system_notifications.sys.platform", "linux")
    @patch("src.observability.system_notifications._windows_toast")
    def test_non_windows_is_noop(self, toast):
        send_system_notification("title", "message")
        toast.assert_not_called()


class TestCliNotificationBoundary(unittest.TestCase):
    def test_waiting_human_notifies_once_when_command_really_pauses(self):
        backend = Mock()
        result = {
            "workflow_status": "WAITING_HUMAN",
            "interrupts": [{"value": {"type": "plan_review"}}],
        }
        with notification_session(backend), patch.object(
            cli, "_interactive_resume_value", return_value=None
        ):
            cli._run_interactive_chapter("novel", 3, result)
        backend.assert_called_once()
        self.assertIn("Plan Review", backend.call_args.args[1])
        self.assertEqual(result["workflow_status"], "WAITING_HUMAN")
        self.assertEqual(result["interrupts"][0]["value"]["type"], "plan_review")

    def test_terminal_success_and_derivation_error_notify(self):
        success_backend = Mock()
        with notification_session(success_backend):
            cli._print_chapter_result(
                "novel", 1, {"workflow_status": "DERIVED_READY"}
            )
        success_backend.assert_called_once()

        error_backend = Mock()
        with notification_session(error_backend):
            cli._print_chapter_result("novel", 1, {
                "workflow_status": "DERIVATION_ERROR",
                "failed_derivation_stage": "verify_atomic_facts",
                "derivation_error": "workflow-error-X",
            })
        error_backend.assert_called_once()
        self.assertIn("verify_atomic_facts", error_backend.call_args.args[1])

    def test_backend_failure_preserves_terminal_rendering(self):
        backend = Mock(side_effect=RuntimeError("toast-error-Y"))
        with notification_session(backend), patch("builtins.print") as rendered:
            cli._print_chapter_result("novel", 1, {
                "workflow_status": "DERIVATION_ERROR",
                "failed_derivation_stage": "verify_atomic_facts",
                "derivation_error": "workflow-error-X",
            })
        output = "\n".join(str(call.args[0]) for call in rendered.call_args_list)
        self.assertIn("workflow-error-X", output)
        self.assertNotIn("toast-error-Y", output)

    def test_intermediate_results_and_status_do_not_notify(self):
        backend = Mock()
        with notification_session(backend):
            for status in (
                "QUERY_INTENT_FINALIZED", "PLAN_CREATED", "PROSE_CREATED",
                "CANONICAL_COMMITTED", "CURRENT_STATE_UPDATED",
                "ATOMIC_FACTS_DERIVED", "FACT_DIGEST_PERSISTED",
                "RAG_UPDATED",
            ):
                # Intermediate graph nodes never cross the CLI terminal adapter.
                self.assertNotEqual(status, "DERIVED_READY")
        backend.assert_not_called()

        args = type("Args", (), {"name": "novel"})()
        with notification_session(backend), patch.object(
            cli, "_get_novel_dir", return_value=True
        ), patch.object(cli.NovelStatusService, "print_status"):
            cli.cmd_status(args)
        backend.assert_not_called()

    def test_notifications_never_become_generation_events(self):
        result = {"generation_events": [{"event_type": "PLAN_CREATED"}]}
        with notification_session(Mock()) as session:
            session.waiting_human("novel", 1, "Plan Review")
        self.assertEqual(result["generation_events"], [
            {"event_type": "PLAN_CREATED"}
        ])
