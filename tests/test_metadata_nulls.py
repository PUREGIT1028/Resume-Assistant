import unittest
from pydantic import ValidationError
from app.models.metadata import ResumeMetadata

class MissingListTests(unittest.TestCase):
    def test_null_lists_from_model(self):
        fields=('work_experience','education','skills','projects','languages','certifications','preferred_locations')
        result=ResumeMetadata(name='测试用户',**dict.fromkeys(fields))
        for field in fields:
            self.assertEqual(getattr(result,field),[])
        self.assertIsNone(result.expected_salary)

    def test_valid_data_unchanged_and_bad_types_rejected(self):
        self.assertEqual(ResumeMetadata(name='测试',preferred_locations=['北京']).preferred_locations,['北京'])
        with self.assertRaises(ValidationError):
            ResumeMetadata(name='测试',preferred_locations={'city':'北京'})

if __name__=='__main__': unittest.main()
