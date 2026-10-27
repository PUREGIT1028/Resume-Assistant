from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.core.llm_client import LLMClient
from app.core.analyzer import CandidateAnalyzer
from app.models.metadata import QueryMetadata


def test_zhipu_uses_fast_mode_and_bounded_timeout(monkeypatch):
    import app.core.llm_client as module
    model = Mock()
    constructor = Mock(return_value=model)
    monkeypatch.setattr(module, 'ChatOpenAI', constructor)
    monkeypatch.setenv('LLM_API_KEY', 'test-key')
    monkeypatch.setenv('LLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4/')
    LLMClient(model_name='glm-4.7-flashx')
    options=constructor.call_args.kwargs
    assert options['extra_body']=={'thinking':{'type':'disabled'}}
    assert options['timeout']==30.0
    assert options['max_retries']==0


def test_empty_model_answer_is_explicit_error():
    client=LLMClient.__new__(LLMClient)
    client.model=Mock()
    client.model.invoke.return_value=SimpleNamespace(content='')
    with pytest.raises(ValueError,match='空内容'):
        client.generate_text('test')


def test_report_failure_keeps_candidate_order_and_scores(monkeypatch):
    analyzer=CandidateAnalyzer(Mock())
    resumes=[{'id':'a','scores':{'overall_score':.8}}, {'id':'b','scores':{'overall_score':.5}}]
    def analyze(resume, query):
        if resume['id']=='a':
            raise TimeoutError()
        return dict(resume,analysis='ok')
    monkeypatch.setattr(analyzer,'analyze_candidate',analyze)
    results=analyzer.analyze_candidates(resumes,QueryMetadata())
    assert [r['id'] for r in results]==['a','b']
    assert results[0]['scores']==resumes[0]['scores']
    assert '暂时不可用' in results[0]['analysis']
    assert results[1]['analysis']=='ok'
