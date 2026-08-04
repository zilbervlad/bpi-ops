from __future__ import annotations

from datetime import date
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.dwp import email_delivery_fix as fix


class FakeUser:
    def __init__(self, email):
        self.email = email

    def get_notification_email(self):
        return self.email


class DWPEmailDeliveryFixTests(unittest.TestCase):
    def record(self):
        return SimpleNamespace(
            id=15,
            team_member_id=101,
            submitted_by_id=202,
            team_member_name_snapshot="Bouhattab Aymen abdelaziz",
            submitted_by_name_snapshot="Mikail Kilic",
            store_number="3718",
            discussion_type="Written Reminder",
            category="Performance",
            conversation_date=date(2026, 8, 2),
            infraction_date=date(2026, 8, 2),
        )

    def settings_model(self, *, enabled=True, recipients=""):
        settings = SimpleNamespace(
            enabled=enabled,
            recipients_text=recipients,
        )
        return SimpleNamespace(
            query=SimpleNamespace(first=lambda: settings)
        )

    def delivery_context(
        self,
        *,
        direct_enabled,
        settings_model,
        team_email=None,
        submitter_email=None,
        send_side_effect=True,
    ):
        if isinstance(send_side_effect, list):
            send_mock = MagicMock(side_effect=send_side_effect)
        else:
            send_mock = MagicMock(return_value=send_side_effect)

        pdf_mock = MagicMock(return_value=BytesIO(b"pdf"))

        patches = [
            patch.object(
                fix,
                "email_event_is_enabled",
                return_value=direct_enabled,
            ),
            patch.object(
                fix,
                "DWPEmailSettings",
                settings_model,
            ),
            patch.object(
                fix.db.session,
                "get",
                side_effect=[
                    FakeUser(team_email) if team_email else None,
                    FakeUser(submitter_email) if submitter_email else None,
                ],
            ),
            patch.object(
                fix,
                "url_for",
                return_value="https://ops.bostonpie.net/dwp/15",
            ),
            patch.object(
                fix.dwp_routes,
                "safe_send_dwp_email",
                send_mock,
            ),
            patch.object(
                fix.dwp_routes,
                "make_dwp_pdf",
                pdf_mock,
            ),
        ]

        return patches, send_mock, pdf_mock

    def test_pdf_distribution_still_sends_when_central_event_is_off(self):
        settings_model = self.settings_model(
            recipients="vlad@bostonpie.com\nhr@bostonpie.com"
        )
        patches, send_mock, pdf_mock = self.delivery_context(
            direct_enabled=False,
            settings_model=settings_model,
            team_email="team@example.com",
            submitter_email="submitter@example.com",
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = fix.send_dwp_created_emails(self.record())

        self.assertEqual(result, (2, 0))
        self.assertEqual(send_mock.call_count, 2)
        self.assertTrue(
            all(
                "attachments" in call.kwargs
                for call in send_mock.call_args_list
            )
        )
        pdf_mock.assert_called_once()

    def test_disabled_delivery_paths_return_a_tuple(self):
        settings_model = self.settings_model(
            enabled=False,
            recipients="vlad@bostonpie.com",
        )
        patches, send_mock, pdf_mock = self.delivery_context(
            direct_enabled=False,
            settings_model=settings_model,
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = fix.send_dwp_created_emails(self.record())

        self.assertEqual(result, (0, 0))
        send_mock.assert_not_called()
        pdf_mock.assert_not_called()

    def test_failed_direct_delivery_does_not_suppress_pdf_retry(self):
        settings_model = self.settings_model(
            recipients="vlad@bostonpie.com"
        )
        patches, send_mock, pdf_mock = self.delivery_context(
            direct_enabled=True,
            settings_model=settings_model,
            team_email="vlad@bostonpie.com",
            send_side_effect=[False, True],
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = fix.send_dwp_created_emails(self.record())

        self.assertEqual(result, (1, 1))
        self.assertEqual(send_mock.call_count, 2)
        self.assertNotIn(
            "attachments",
            send_mock.call_args_list[0].kwargs,
        )
        self.assertIn(
            "attachments",
            send_mock.call_args_list[1].kwargs,
        )
        pdf_mock.assert_called_once()

    def test_successful_direct_delivery_prevents_duplicate_pdf(self):
        settings_model = self.settings_model(
            recipients="vlad@bostonpie.com"
        )
        patches, send_mock, pdf_mock = self.delivery_context(
            direct_enabled=True,
            settings_model=settings_model,
            team_email="vlad@bostonpie.com",
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = fix.send_dwp_created_emails(self.record())

        self.assertEqual(result, (1, 0))
        self.assertEqual(send_mock.call_count, 1)
        pdf_mock.assert_not_called()

    def test_fix_is_installed_on_dwp_routes(self):
        self.assertIs(
            fix.dwp_routes.send_dwp_created_emails,
            fix.send_dwp_created_emails,
        )


if __name__ == "__main__":
    unittest.main()
