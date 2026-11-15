import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.error_alerts import send_error_alert


class ErrorAlertTests(SimpleTestCase):
    @override_settings(ERROR_ALERT_EMAIL='ops@example.test', DEBUG=False)
    def test_alert_returns_before_a_slow_mail_host_answers(self):
        started = threading.Event()
        release = threading.Event()

        def slow_send(*args, **kwargs):
            started.set()
            release.wait(2)

        with patch('core.error_alerts.send_mail', side_effect=slow_send):
            with patch('core.error_alerts.get_connection', return_value=object()):
                began = time.monotonic()
                queued = send_error_alert(subject='Dispatch failed', body='Original exception')
                elapsed = time.monotonic() - began
        self.assertTrue(queued)
        self.assertLess(elapsed, 0.5)
        release.set()
        self.assertTrue(started.wait(2))
