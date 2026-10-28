## @file
# @brief 평가 공통 지표(kappa, Wilson 구간, 가중 kappa, recall·precision)가 알려진 값과 같고, 경계 입력을 정해 둔 규칙대로 처리하는지 검증
# - Task 3(Mohler)·4(한국어 채점)·5(품질 하한)·2(M1·M2)가 같은 함수를 쓰므로, 경계 규칙이 바뀌면 여기서 먼저 깨지게 함
import random
import unittest

from eval.metrics import cohen_kappa, quadratic_weighted_kappa, recall_precision, wilson_interval


class CohenKappaTest(unittest.TestCase):
    # 검증 목적: 두 채점자가 모든 답에서 같으면 1.0, 정반대면 -1.0
    def test_perfect_and_opposite(self):
        a = [True, False, True, True, False]
        self.assertEqual(cohen_kappa(a, a), 1.0)
        self.assertAlmostEqual(cohen_kappa([True, False] * 3, [False, True] * 3), -1.0)

    # 검증 목적: 서로 독립인 판정(우연 수준)은 0 근처
    # - 정확히 독립인 배치는 0, 무작위 2000개는 표본 오차 안에서 0에 가까움
    def test_chance_level_is_near_zero(self):
        self.assertAlmostEqual(cohen_kappa([True, True, False, False] * 5, [True, False, True, False] * 5), 0.0)
        rng = random.Random(7)
        a = [rng.random() < 0.7 for _ in range(2000)]
        b = [rng.random() < 0.7 for _ in range(2000)]
        self.assertLess(abs(cohen_kappa(a, b)), 0.05)

    # 검증 목적: 손으로 계산한 값과 같은지 확인(일치율 0.8, 우연 일치 0.5 → 0.6)
    def test_known_value(self):
        a = [True] * 5 + [False] * 5
        b = [True] * 4 + [False] + [True] + [False] * 4
        self.assertAlmostEqual(cohen_kappa(a, b), 0.6)

    # 검증 목적: (Review Focus 4) 두 채점자가 모두 한 범주만 쓴 경우 우연 일치가 1이라 식이 0/0이 됨
    # - 이때 두 판정은 반드시 모두 같으므로 1.0으로 정함(나눗셈 오류나 NaN으로 보고서가 깨지지 않게)
    # - 한쪽만 한 범주면 우연 일치가 1보다 작아 보통 식으로 계산됨
    def test_single_category(self):
        self.assertEqual(cohen_kappa([True] * 4, [True] * 4), 1.0)
        self.assertEqual(cohen_kappa([False] * 3, [False] * 3), 1.0)
        self.assertAlmostEqual(cohen_kappa([True] * 4, [True, True, True, False]), 0.0)

    # 검증 목적: 빈 입력과 길이가 다른 입력은 조용히 숫자를 내지 않고 ValueError
    # - 빈 답·한쪽만 채점한 답은 호출하는 쪽(일치도 모듈)이 먼저 빼고 남은 개수를 함께 보고해야 함
    def test_empty_and_length_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            cohen_kappa([], [])
        with self.assertRaises(ValueError):
            cohen_kappa([True, False], [True])


class WilsonIntervalTest(unittest.TestCase):
    # 검증 목적: 계획의 기준값(1/50의 95% 상한 약 0.105)과 대칭성, 0·n 끝값의 범위
    def test_known_values(self):
        low, high = wilson_interval(1, 50)
        self.assertAlmostEqual(high, 0.105, places=3)
        self.assertGreater(low, 0.0)
        self.assertLess(low, 0.02)
        low0, high0 = wilson_interval(0, 50)
        self.assertAlmostEqual(low0, 0.0)
        self.assertAlmostEqual(high0, 1 - wilson_interval(50, 50)[0])
        self.assertAlmostEqual(wilson_interval(50, 50)[1], 1.0)

    # 검증 목적: 표본이 0개면 정보가 없으므로 [0, 1] 전체, 개수가 범위 밖이면 ValueError
    def test_edge_inputs(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))
        for k, n in ((-1, 5), (6, 5)):
            with self.assertRaises(ValueError):
                wilson_interval(k, n)


class QuadraticWeightedKappaTest(unittest.TestCase):
    # 검증 목적: 완전 일치 1.0, 손으로 계산한 값, 점수 차가 클수록 더 크게 깎이는지 확인
    def test_known_values(self):
        labels = [0, 1, 2, 3, 4, 5]
        a = [0, 1, 2, 3, 4, 5]
        self.assertEqual(quadratic_weighted_kappa(a, a, labels), 1.0)
        # 3개 중 1개가 한 칸 차이: 관측 가중 불일치 1/12 ÷ 기대 가중 불일치 1/4 → 1 - 1/3
        self.assertAlmostEqual(quadratic_weighted_kappa([0, 1, 2], [0, 1, 1], [0, 1, 2]), 2 / 3)
        near = quadratic_weighted_kappa(a, [0, 1, 2, 3, 4, 4], labels)
        far = quadratic_weighted_kappa(a, [5, 1, 2, 3, 4, 5], labels)
        self.assertGreater(near, far)

    # 검증 목적: 한 범주만 쓴 경우(기대 불일치 0)는 cohen_kappa와 같은 규칙으로 1.0, 빈 입력·labels 밖 값은 ValueError
    def test_edge_inputs(self):
        self.assertEqual(quadratic_weighted_kappa([3, 3], [3, 3], [0, 1, 2, 3]), 1.0)
        self.assertEqual(quadratic_weighted_kappa([1], [1], [1]), 1.0)
        for a, b in (([], []), ([1, 2], [1]), ([1, 9], [1, 2])):
            with self.assertRaises(ValueError):
                quadratic_weighted_kappa(a, b, [0, 1, 2])


class RecallPrecisionTest(unittest.TestCase):
    # 검증 목적: 일반 값과, 빈 집합 규칙(찾을 것이 없으면 recall 1, 말한 것이 없으면 precision 1)
    # - M1에서 빈틈 0개 프로필에 엔진이 빈틈을 하나도 말하지 않으면 recall·precision 모두 1이어야 함
    def test_values_and_empty_sets(self):
        self.assertEqual(recall_precision({'a', 'b', 'x'}, {'a', 'b', 'c', 'd'}), (0.5, 2 / 3))
        self.assertEqual(recall_precision(set(), set()), (1.0, 1.0))
        self.assertEqual(recall_precision({'a'}, set()), (1.0, 0.0))
        self.assertEqual(recall_precision(set(), {'a'}), (0.0, 1.0))


if __name__ == '__main__':
    unittest.main()
