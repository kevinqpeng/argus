import unittest
from question_guard import prepare_questions


class QuestionGuardTests(unittest.TestCase):
    def test_deduplicates_batch_and_keeps_unanswered(self):
        question = {"id": "human_case_review", "question": "是否需要人工评审？"}
        other = {"id": "scope", "question": "测试范围？"}
        self.assertEqual(prepare_questions([question, dict(question), other], {}), [question, other])

    def test_answered_false_is_not_asked_again(self):
        question = {"id": "human_case_review", "question": "是否需要人工评审？"}
        self.assertEqual(prepare_questions([question], {"human_case_review": False}), [])

    def test_conflicting_definitions_are_rejected(self):
        with self.assertRaises(ValueError):
            prepare_questions([{"id": "scope", "question": "范围？"},
                               {"id": "scope", "question": "其他问题？"}], {})


if __name__ == "__main__":
    unittest.main()
