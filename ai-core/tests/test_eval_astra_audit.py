## @file
# @brief 외부 검사 모델(astra 등)로 노출 문장을 다시 판정하는 eval.astra_audit 검증(실제 codex 호출 없음)
# - 검사 호출이 실패한 문장을 불일치로 세면 노출 지표가 부풀고, 일치로 세면 줄어 합격 판정이 틀어짐
import unittest

from eval.astra_audit import audit, build_prompt, exposure


## @brief 두 번째 호출에서 예외를 내고, 나머지 호출은 id 순서대로 [틀림, 맞음, …]을 돌려주는 가짜 runner
class FlakyRunner:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        if len(self.prompts) == 2:
            raise RuntimeError('codex 실패')
        return {'results': [{'id': i, 'supported': i % 2 == 1} for i in range(40)]}


class AstraAuditTests(unittest.TestCase):
    # 검증 목적: 한 배치의 검사가 실패하면 그 배치 항목만 None이 되고, 노출 계산은 None을 분모·분자에서 빼고 실패 개수로 보고함
    def test_audit_none_is_not_mismatch(self):
        items = [{'sentence': f'문장 {n}', 'passage': f'문단 {n}'} for n in range(5)]
        runner = FlakyRunner()
        results = audit(items, runner=runner, batch=2)
        self.assertEqual(results, [False, True, None, None, False])
        self.assertEqual(len(runner.prompts), 3)
        self.assertEqual(exposure(results), {'audited': 3, 'mismatch': 2, 'errors': 2, 'rate': 0.6667})

    # 검증 목적: 검사 모델에 보내는 내용은 지시문과 공개 교재 문단·모델 문장뿐이어야 함(다른 파일·비밀정보를 보내지 않음)
    def test_prompt_contains_only_items(self):
        prompt = build_prompt([{'sentence': '스택은 LIFO다.', 'passage': 'A stack is LIFO.', 'secret': 'x'}])
        self.assertIn('스택은 LIFO다.', prompt)
        self.assertIn('A stack is LIFO.', prompt)
        self.assertNotIn('secret', prompt)


if __name__ == '__main__':
    unittest.main()
