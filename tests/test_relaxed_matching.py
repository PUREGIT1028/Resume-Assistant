import unittest
from unittest.mock import Mock
from app.models.metadata import QueryMetadata
from app.core.filter import HardFilter
from app.core.relaxed_matching import relaxed_candidates
from app.core.analyzer import CandidateAnalyzer
from app.core.scorer import Scorer
from app.core.ranker import Ranker

class RelaxedMatchingTests(unittest.TestCase):
    def setUp(self):
        self.records={'one':{'text':'Python 项目','metadata':{'name':'示例甲','skills':['Python']}},'two':{'text':'SQL 项目','metadata':{'name':'示例乙','skills':['SQL']}}}
        self.query=QueryMetadata(required_skills=['Python','Java'],min_experience_years=5)

    def test_partial_matches_are_kept_with_notes(self):
        rows=relaxed_candidates([{'id':'one'},{'id':'deleted'}],self.records,self.query,HardFilter())
        self.assertEqual({r['id'] for r in rows},{'one','two'})
        self.assertTrue(all(r['match_notes'] for r in rows))
        ranked=Ranker().rank_resumes(Scorer().score_resumes(rows,self.query),self.query)
        self.assertEqual(ranked[0]['id'],'one')
        self.assertLess(ranked[0]['scores']['skill_score'],1)

    def test_empty_retrieval_uses_active_records_only(self):
        self.assertEqual(len(relaxed_candidates([],self.records,self.query,HardFilter())),2)
        self.assertEqual(relaxed_candidates([],{},self.query,HardFilter()),[])

    def test_report_includes_evidence_and_full_conditions(self):
        client=Mock(); client.generate_text.return_value='## 1. 综合结论\n部分匹配，需核实。'
        analyzer=CandidateAnalyzer(client)
        row=relaxed_candidates([],self.records,self.query,HardFilter())[0]
        result=analyzer.analyze_candidate(row,self.query)
        prompt=client.generate_text.call_args.args[0]
        self.assertIn('Python 项目',prompt)
        self.assertIn('面试建议',prompt)
        self.assertIn('未提及',prompt)
        self.assertEqual(result['match_notes'],row['match_notes'])
        self.assertIn('综合结论',result['analysis'])

    def test_model_failure_still_provides_grounded_report(self):
        client=Mock();client.generate_text.side_effect=TimeoutError()
        rows=relaxed_candidates([],self.records,self.query,HardFilter())
        result=CandidateAnalyzer(client).analyze_candidates(rows,self.query)
        self.assertEqual(len(result),2)
        self.assertIn('本地规则分析',result[0]['analysis'])
        self.assertIn('面试建议',result[0]['analysis'])
        self.assertIn('Java',result[0]['analysis'])
