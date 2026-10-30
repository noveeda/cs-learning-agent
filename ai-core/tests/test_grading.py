## @file
# @brief 결정적 채점이 표기 차이는 허용하고 내용 차이는 오답으로 판정하는지 검증
import unittest

from serving.grading import grade
from serving.item_bank import Item


def item(**fields):
    base = {'id': 'array-d9', 'concept': 'array', 'purpose': 'diagnosis', 'objective': 0, 'rationale': '근거'}
    base.update(fields)
    return Item.model_validate(base)


class GradingTests(unittest.TestCase):
    # 검증 목적: 선택형은 공백·소문자 입력도 같은 라벨로 인정하고 다른 라벨은 오답
    def test_multiple_choice(self):
        choice = item(format='multiple_choice', question='질문', options={'A': '1', 'B': '2', 'C': '3', 'D': '4'}, answer='B')
        self.assertTrue(grade(choice, ' b '))
        self.assertFalse(grade(choice, 'A'))
        self.assertFalse(grade(choice, '2'))

    # 검증 목적: 단답형은 공백·전각 쉼표 차이를 허용하지만 순서·값이 다르면 오답
    def test_short_exact(self):
        short = item(format='short_exact', question='질문', answer='3,8,5')
        self.assertTrue(grade(short, '3, 8, 5'))
        self.assertTrue(grade(short, '3，8，5'))
        self.assertFalse(grade(short, '5,8,3'))
        self.assertFalse(grade(short, '3,8'))

    # 검증 목적: 값과 순서가 맞으면 쉼표 대신 공백·화살표를 쓰거나, 한 글자 값이면 구분자를 빼도 정답(막힘 확인에서 '321123'을 틀림으로 본 문제)
    def test_short_exact_separator_variants(self):
        single = item(format='short_exact', question='질문', answer='3,2,1,1,2,3')
        self.assertTrue(grade(single, '321123'))
        self.assertTrue(grade(single, '3 2 1 1 2 3'))
        self.assertTrue(grade(single, '3->2->1->1->2->3'))
        self.assertTrue(grade(single, '3 → 2 → 1 → 1 → 2 → 3'))
        self.assertFalse(grade(single, '123321'))
        self.assertFalse(grade(single, '32112'))
        # 여러 자리 값은 구분자가 없으면 어디서 끊는지 알 수 없어 오답으로 둠
        multi = item(format='short_exact', question='질문', answer='14,20,26,8')
        self.assertTrue(grade(multi, '14 20 26 8'))
        self.assertFalse(grade(multi, '1420268'))
        self.assertFalse(grade(multi, '14,20,2,68'))

    # 검증 목적: 서술 문항을 결정적 채점기로 잘못 판정하지 않도록 거부
    def test_rejects_explanation(self):
        explanation = item(id='array-e9', purpose='explanation', format='explanation', question='설명하세요',
                           rubric=['가', '나'])
        with self.assertRaises(ValueError):
            grade(explanation, '아무 답')


if __name__ == '__main__':
    unittest.main()
