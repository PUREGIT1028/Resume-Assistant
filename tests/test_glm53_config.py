import os
import unittest
from unittest.mock import patch
from app.core.llm_client import LLMClient

class GLM53ConfigTests(unittest.TestCase):
    @patch('app.core.llm_client.ChatOpenAI')
    def test_glm53_requires_thinking_and_longer_budget(self, client):
        with patch.dict(os.environ, {'LLM_API_KEY':'test','LLM_BASE_URL':'https://open.bigmodel.cn/api/paas/v4/'}):
            LLMClient(model_name='glm-5.3-flash',temperature=0)
        options=client.call_args.kwargs
        self.assertEqual(options['extra_body']['thinking']['type'],'enabled')
        self.assertEqual(options['temperature'],1.0)
        self.assertEqual(options['timeout'],90.0)
        self.assertEqual(options['max_tokens'],16384)

    @patch('app.core.llm_client.ChatOpenAI')
    def test_glm47_keeps_previous_options(self, client):
        with patch.dict(os.environ, {'LLM_API_KEY':'test','LLM_BASE_URL':'https://open.bigmodel.cn/api/paas/v4/'}):
            LLMClient(model_name='glm-4.7-flashx',temperature=0)
        options=client.call_args.kwargs
        self.assertEqual(options['extra_body']['thinking']['type'],'disabled')
        self.assertEqual(options['timeout'],30.0)
