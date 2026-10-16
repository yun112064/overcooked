import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import llm_cooking_estimator as m

class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode())

class GeminiTests(unittest.TestCase):
    def call(self, statuses, models=None, message=None):
        calls = []
        def transport(req, **kw):
            calls.append(req)
            if ':generateContent' not in req.full_url:
                return Response({'models': models or [
                    {'name': 'models/gemini-3.5-flash-lite', 'supportedGenerationMethods': ['generateContent']},
                    {'name': 'models/gemini-embedding', 'supportedGenerationMethods': ['embedContent']}]})
            status = statuses.pop(0)
            if status != 200:
                raise HTTPError(req.full_url, status, 'failed', {}, Response({'error': {
                    'status': 'NOT_FOUND', 'message': message or 'model missing key=secret-value AIzaTESTSECRET'}}))
            return Response({'candidates': [{'content': {'parts': [{'text': json.dumps({'ok': True})}]}}]})
        log = io.StringIO()
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'secret-value', 'GEMINI_MODEL': 'gemini-old'}, clear=True), patch.object(m, 'urlopen', transport), patch('time.sleep'), contextlib.redirect_stderr(log):
            result = m.gemini_estimator({})
        return result, calls, log.getvalue()

    def test_404_uses_only_listed_generate_content_models(self):
        result, calls, log = self.call([404, 200])
        self.assertEqual(result, {'ok': True})
        self.assertIn('models/gemini-3.5-flash-lite:generateContent', calls[-1].full_url)
        self.assertNotIn('secret-value', log)
        self.assertNotIn('AIzaTESTSECRET', log)
        self.assertIn('NOT_FOUND', log)
        self.assertIn('model missing', log)
        self.assertTrue(all('key=' not in r.full_url for r in calls))

    def test_auth_failure_does_not_fallback(self):
        result, calls, log = self.call([403])
        self.assertFalse(result['success'])
        self.assertEqual(len(calls), 1)

    def test_503_retries_then_uses_listed_model(self):
        result, calls, log = self.call([503, 503, 200])
        self.assertEqual(result, {'ok': True})
        self.assertEqual(calls[0].full_url, calls[1].full_url)

    def test_no_supported_model_fails_closed(self):
        result, calls, log = self.call([404], [{'name': 'models/embed', 'supportedGenerationMethods': ['embedContent']}])
        self.assertFalse(result['success'])
        self.assertEqual(len(calls), 2)

    def test_retired_model_uses_listed_server_recommendation_first(self):
        models = [{'name': 'models/' + n, 'supportedGenerationMethods': ['generateContent']}
                  for n in ['gemini-2.5-flash-lite', 'gemini-3.1-flash-lite',
                            'gemini-3.5-flash-lite', 'gemini-3.8-flash']]
        result, calls, log = self.call([404, 200], models,
            'This model is no longer available to new users. Please update your code to use models/gemini-3.8-flash')
        self.assertEqual(result, {'ok': True})
        self.assertTrue(calls[-1].full_url.endswith('/gemini-3.8-flash:generateContent'))

    def test_429_stops_without_retry_or_fallback(self):
        result, calls, log = self.call([429])
        self.assertFalse(result['success'])
        self.assertEqual(len(calls), 1)

    def test_unlisted_recommendation_is_never_called(self):
        result, calls, log = self.call([404, 200], message='Use models/gemini-unlisted-flash')
        self.assertEqual(result, {'ok': True})
        self.assertTrue(calls[-1].full_url.endswith('/gemini-3.5-flash-lite:generateContent'))

if __name__ == '__main__':
    unittest.main()
