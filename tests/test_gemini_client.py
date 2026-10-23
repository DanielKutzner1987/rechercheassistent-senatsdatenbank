import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import gemini_client as target


class ProviderTransportTests(unittest.TestCase):
    def test_quota_and_auth_errors_are_not_retried(self):
        for code in (400, 401, 403, 404, 429):
            with self.subTest(code=code):
                exc = HTTPError('https://example.invalid', code, 'test', {}, io.BytesIO(b'{}'))
                with patch.object(target.request, 'urlopen', side_effect=exc) as opened, patch.object(target.time, 'sleep') as sleep:
                    with self.assertRaises(HTTPError):
                        target.request_json(object(), 5)
                self.assertEqual(opened.call_count, 1)
                sleep.assert_not_called()

    def test_unavailability_is_bounded_and_secret_is_redacted(self):
        def unavailable(*args, **kwargs):
            raise HTTPError('https://example.invalid', 503, 'test', {}, io.BytesIO(b'{}'))
        with patch.object(target.request, 'urlopen', side_effect=unavailable) as opened, patch.object(target.time, 'sleep'):
            with self.assertRaises(HTTPError):
                target.request_json(object(), 5)
        self.assertEqual(opened.call_count, 3)
        problem = target.safe_api_error(json.dumps({'error': {'message': 'EXAMPLE_PRIVATE_KEY'}}), 'EXAMPLE_PRIVATE_KEY', 400)
        self.assertNotIn('EXAMPLE_PRIVATE_KEY', json.dumps(problem))

    def test_malformed_key_is_rejected_before_request(self):
        for value in ('', 'unrelated clipboard content', 'AIza' + 'x' * 35 + '\nInjected: bad'):
            self.assertFalse(target.prepare_key(value)[1]['valid_http_token'])
