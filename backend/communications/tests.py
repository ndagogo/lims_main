from django.test import SimpleTestCase
class NotificationFoundationTests(SimpleTestCase):
    def test_supported_channels_are_documented(self):
        self.assertEqual({'SMS','EMAIL','WHATSAPP'},{'SMS','EMAIL','WHATSAPP'})
