"""Isolated model transport diagnostics, retry compatibility and credential redaction."""
import io
import json
import os
import socket
import ssl
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SERVER = Path(__file__).resolve().parents[1] / 'server'
IMPORT_DIR = Path(tempfile.mkdtemp(prefix='zhixing_model_errors_'))
os.environ.update(ZHIXING_DATA_DIR=str(IMPORT_DIR), ZHIXING_DB_PATH=str(IMPORT_DIR/'zhixing.db'),
                  ZHIXING_STORAGE_DIR=str(IMPORT_DIR/'storage'))
sys.path.insert(0,str(SERVER))
from test_env_helper import setup_test_db, cleanup_test_db
import deepseek_extractor as de
from model_errors import ModelRequestError, classify_model_error


def http_error(status, message='provider failed', code=''):
    return urllib.error.HTTPError('https://example.invalid',status,'failed',{},
        io.BytesIO(json.dumps({'error':{'message':message,'code':code}}).encode()))


class Response:
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return b'{"choices":[{"message":{"content":"{}"}}]}'


class TestModelErrors(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.db=setup_test_db('model_errors')
    @classmethod
    def tearDownClass(cls): cleanup_test_db(cls.db)

    def test_http_error_classification(self):
        for status,code,retry in ((400,'invalid_request',False),(401,'authentication',False),
                (402,'quota',False),(403,'permission',False),(429,'rate_limit',True),
                (502,'service_unavailable',True),(503,'service_unavailable',True),(504,'service_unavailable',True)):
            with self.subTest(status=status):
                error=classify_model_error(http_error(status))
                self.assertEqual(error.code,code);self.assertEqual(error.http_status,status)
                self.assertEqual(error.retryable,retry);self.assertIn(str(status),str(error))

    def test_quota_and_context_errors_are_not_transient(self):
        quota=classify_model_error(http_error(429,code='insufficient_quota'))
        context=classify_model_error(http_error(400,'maximum context length exceeded'))
        self.assertEqual(quota.code,'quota');self.assertFalse(quota.retryable)
        self.assertEqual(context.code,'context_limit');self.assertFalse(context.retryable)

    def test_network_timeout_and_tls(self):
        for raw,code,retry in ((socket.timeout('timeout secret'),'timeout',True),
                (urllib.error.URLError(socket.timeout('secret')),'timeout',True),
                (urllib.error.URLError('network secret'),'network',True),
                (urllib.error.URLError(ssl.SSLError('secret')),'tls',False)):
            with self.subTest(code=code):
                error=classify_model_error(raw)
                self.assertEqual(error.code,code);self.assertEqual(error.retryable,retry)
                self.assertNotIn('secret',json.dumps(error.diagnostic()))

    def test_wrapper_records_http_status_without_credentials_or_provider_body(self):
        secret='sensitive-key';raw=http_error(401,'Bearer '+secret+' Token=private-token')
        with patch.object(de.urllib.request,'urlopen',side_effect=raw), self.assertLogs(de.logger,level='WARNING') as logs:
            with self.assertRaises(ModelRequestError) as raised:
                de.post_chat_completion([],api_key=secret,max_attempts=1)
        self.assertEqual(raised.exception.http_status,401)
        saved=' '.join(logs.output)+str(raised.exception)+json.dumps(raised.exception.diagnostic())
        self.assertNotIn(secret,saved);self.assertNotIn('private-token',saved)

    def test_existing_wrapper_retry_count_and_success_are_preserved(self):
        with patch.object(de.urllib.request,'urlopen',side_effect=[http_error(503),Response()]) as call:
            result=de.post_chat_completion([],api_key='test-key',max_attempts=2)
        self.assertEqual(result[0],'{}');self.assertEqual(call.call_count,2)

    def test_existing_wrapper_exhaustion_is_runtimeerror_compatible(self):
        with patch.object(de.urllib.request,'urlopen',side_effect=[http_error(502),http_error(502)]) as call:
            with self.assertRaises(RuntimeError) as raised:
                de.post_chat_completion([],api_key='test-key',max_attempts=2)
        self.assertIsInstance(raised.exception,ModelRequestError)
        self.assertEqual(call.call_count,2);self.assertEqual(raised.exception.http_status,502)

    def test_malformed_response_classified_without_leaking_raw_content(self):
        class BadResponse(Response):
            def read(self):return b'{"error":"sensitive-key"}'
        with patch.object(de.urllib.request,'urlopen',return_value=BadResponse()):
            with self.assertRaises(ModelRequestError) as raised:
                de.post_chat_completion([],api_key='sensitive-key',max_attempts=1)
        self.assertEqual(raised.exception.code,'invalid_response')
        self.assertNotIn('sensitive-key',str(raised.exception))


if __name__=='__main__':unittest.main(verbosity=2)
