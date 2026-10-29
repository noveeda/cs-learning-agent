## @file
# @brief 진단 문항 지문 도식의 구조 규칙·정답 누설 검사·라벨 길이·겹침 검사와 문항 모델 연결을 검증
import unittest

from pydantic import ValidationError

from serving.figure import Figure, check_figure, column_widths, label_width
from serving.item_bank import Item

QUESTION = '간선이 1→2:6, 1→3:2, 3→2:3인 방향 그래프에서 정점 1부터 정점 2까지의 최단 거리는? 숫자만 쓰세요.'


## @brief 지문의 사실만 그린 정상 그래프 도식; 각 테스트가 필요한 부분만 바꿔 씀
def graph(**changes):
    figure = {
        'nodes': [{'id': 'n1', 'label': '1', 'shape': 'circle', 'x': 0, 'y': 1},
                  {'id': 'n2', 'label': '2', 'shape': 'circle', 'x': 2, 'y': 0},
                  {'id': 'n3', 'label': '3', 'shape': 'circle', 'x': 2, 'y': 2}],
        'edges': [{'from': 'n1', 'to': 'n2', 'label': '6'},
                  {'from': 'n1', 'to': 'n3', 'label': '2'},
                  {'from': 'n3', 'to': 'n2', 'label': '3'}],
    }
    figure.update(changes)
    return figure


## @brief 도식을 검사해 오류 문장 목록 반환; 구조 오류는 모델 검증 단계에서 ValueError로 바뀜
def errors(figure, question=QUESTION):
    return check_figure(Figure.model_validate(figure), question)


class FigureStructureTests(unittest.TestCase):
    # 검증 목적: 지문의 간선을 그대로 옮긴 도식은 모든 검사를 통과해 규칙이 정상 도식을 막지 않는지 확인
    def test_accepts_graph_from_question_facts(self):
        self.assertEqual(errors(graph()), [])

    # 검증 목적: 강조·결과 상태 같은 필드를 둘 곳이 없어, 최단 경로 강조 같은 정답 표시를 데이터에 넣을 수 없게 함
    def test_rejects_highlight_fields(self):
        nodes = graph()['nodes']
        with self.assertRaises(ValidationError):
            Figure.model_validate(graph(nodes=[{**nodes[0], 'tone': 'active'}, *nodes[1:]]))
        with self.assertRaises(ValidationError):
            Figure.model_validate(graph(edges=[{'from': 'n1', 'to': 'n2', 'label': '6', 'highlight': True}]))

    # 검증 목적: 선이 없는 도형을 가리키거나 ID가 겹치면 그릴 수 없으므로 거부
    def test_rejects_dangling_edge_and_duplicate_id(self):
        with self.assertRaisesRegex(ValidationError, '없는 도형'):
            Figure.model_validate(graph(edges=[{'from': 'n1', 'to': 'n9'}]))
        nodes = graph()['nodes']
        with self.assertRaisesRegex(ValidationError, '중복'):
            Figure.model_validate(graph(nodes=[*nodes, {**nodes[0], 'x': 4}]))

    # 검증 목적: 좌표가 격자 밖이거나 반 칸 단위가 아니면 화면 폭을 넘거나 배치가 어긋나므로 거부
    def test_rejects_out_of_grid_coordinates(self):
        nodes = graph()['nodes']
        for bad in ({'x': 9}, {'x': 0.3}, {'y': -1}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                Figure.model_validate(graph(nodes=[{**nodes[0], **bad}, *nodes[1:]]))

    # 검증 목적: 값이 정해지지 않은 빈 칸(가득 찬 배열의 자리 등)과 이름 없는 표는 허용하고, 다른 모양의 빈 라벨은 거부
    def test_allows_empty_cell_and_unnamed_table(self):
        figure = {'nodes': [{'id': f'c{i}', 'label': '', 'shape': 'cell', 'x': i / 2, 'y': 0} for i in range(4)],
                  'tables': [{'id': 't', 'columns': ['k'], 'x': 0, 'y': 1}]}
        self.assertEqual(errors(figure, '키 k'), [])
        with self.assertRaises(ValidationError):
            Figure.model_validate({'nodes': [{'id': 'a', 'label': '', 'shape': 'box', 'x': 0, 'y': 0}]})

    # 검증 목적: 도형도 표도 없는 빈 도식은 그릴 내용이 없으므로 null(도식 없음)로 두게 함
    def test_rejects_empty_figure(self):
        with self.assertRaises(ValidationError):
            Figure.model_validate({'nodes': [], 'edges': []})


class FigureLeakTests(unittest.TestCase):
    # 검증 목적: 지문에 없는 숫자(계산한 최단 거리 5 등)를 라벨로 쓰면 정답 누설이므로 찾아냄
    def test_flags_label_not_in_question(self):
        nodes = graph()['nodes']
        found = errors(graph(nodes=[*nodes, {'id': 'd', 'label': '거리 5', 'shape': 'text', 'x': 4, 'y': 1}]))
        self.assertTrue(any('거리 5' in e and '지문' in e for e in found), found)

    # 검증 목적: 표의 이름·열·값도 라벨과 같은 규칙으로 지문에 있는 글만 쓰게 함
    def test_flags_table_values_not_in_question(self):
        question = 'Sale(shop,amount)의 행은 (a,4),(b,7)이다. 합계가 5 이상인 shop은?'
        table = {'id': 't', 'name': 'Sale', 'columns': ['shop', 'amount'], 'rows': [['a', '4'], ['b', '7']], 'x': 0, 'y': 0}
        self.assertEqual(errors({'tables': [table]}, question), [])
        found = errors({'tables': [{**table, 'rows': [['a', '4'], ['b', '11']]}]}, question)
        self.assertTrue(any('11' in e for e in found), found)

    # 검증 목적: 지문의 유니코드 빼기(−)와 라벨의 하이픈 빼기(-)를 같은 글자로 보아 음수 가중치 도식을 막지 않음
    def test_label_match_treats_minus_signs_alike(self):
        figure = {'nodes': [{'id': 'a', 'label': '-2', 'shape': 'circle', 'x': 0, 'y': 0}]}
        self.assertEqual(errors(figure, '간선 3→2:−2'), [])

    # 검증 목적: 공백·대소문자 차이만 있는 라벨은 지문의 같은 글로 인정해 정상 도식을 막지 않음
    def test_label_match_ignores_spacing_and_case(self):
        question = '준비 큐에는 P2, P3가 있다.'
        figure = {'nodes': [{'id': 'q', 'label': '준비큐', 'shape': 'text', 'x': 0, 'y': 0},
                            {'id': 'p', 'label': 'p2', 'shape': 'cell', 'x': 2, 'y': 0}]}
        self.assertEqual(errors(figure, question), [])


class FigureLayoutTests(unittest.TestCase):
    # 검증 목적: 한글은 영숫자보다 넓게 계산해, 상자 폭을 넘는 라벨을 그리기 전에 찾음
    def test_label_width_counts_wide_characters(self):
        self.assertGreater(label_width('스택포인터', 15), label_width('stack', 15))

    # 검증 목적: 도형 안에 들어가지 않는 긴 라벨은 글자가 겹치거나 잘리므로 찾아냄(글 최소화 원칙)
    def test_flags_label_wider_than_shape(self):
        question = '원형 노드 서브넷마스크가 있다.'
        found = errors({'nodes': [{'id': 'a', 'label': '서브넷마스크', 'shape': 'circle', 'x': 0, 'y': 0}]}, question)
        self.assertTrue(any('폭' in e for e in found), found)

    # 검증 목적: 표 열 폭은 가장 긴 값에 맞춰 넓어져(최소 80px) 라우팅 표의 주소 같은 긴 값도 칸 안에 들어감
    def test_table_columns_widen_for_long_values(self):
        table = {'id': 't', 'name': '라우팅 표', 'columns': ['목적지', '다음 홉'], 'rows': [['10.20.8.0/21', 'R2']], 'x': 0, 'y': 0}
        widths = column_widths(Figure.model_validate({'tables': [table]}).tables[0])
        self.assertGreater(widths[0], 80)
        self.assertEqual(widths[1], 80)
        self.assertEqual(errors({'tables': [table]}, '라우팅 표: 목적지 10.20.8.0/21의 다음 홉은 R2'), [])

    # 검증 목적: 같은 자리에 겹친 도형은 서로 가리므로 찾아냄; 맞닿은 칸(cell)은 배열처럼 이어 그릴 수 있게 허용
    def test_flags_overlap_but_allows_touching_cells(self):
        nodes = graph()['nodes']
        found = errors(graph(nodes=[*nodes[:2], {**nodes[2], 'x': 2, 'y': 0.5}]))
        self.assertTrue(any('겹' in e for e in found), found)
        cells = {'nodes': [{'id': f'c{i}', 'label': str(i + 1), 'shape': 'cell', 'x': i / 2, 'y': 0} for i in range(3)]}
        self.assertEqual(errors(cells), [])

    # 검증 목적: 선이 양 끝이 아닌 다른 도형을 지나가면 어느 도형을 잇는지 헷갈리므로 찾아냄
    def test_flags_edge_through_other_shape(self):
        nodes = graph()['nodes']
        line = [{**nodes[0], 'x': 0, 'y': 0}, {**nodes[1], 'x': 4, 'y': 0}, {**nodes[2], 'x': 2, 'y': 0}]
        found = errors(graph(nodes=line, edges=[{'from': 'n1', 'to': 'n2', 'label': '6'}]))
        self.assertTrue(any('지나' in e for e in found), found)

    # 검증 목적: 두 선이 엇갈리면 선 라벨이 겹쳐 어느 값이 어느 선인지 헷갈리므로 찾아냄; 끝점을 공유하는 선은 교차가 아님
    def test_flags_crossing_edges(self):
        square = [{'id': f'n{i}', 'label': str(i), 'shape': 'circle', 'x': x, 'y': y}
                  for i, (x, y) in enumerate(((0, 0), (2, 0), (2, 2), (0, 2)), 1)]
        crossing = errors({'nodes': square, 'edges': [{'from': 'n1', 'to': 'n3'}, {'from': 'n2', 'to': 'n4'}]}, '1 2 3 4')
        self.assertTrue(any('교차' in e for e in crossing), crossing)
        ring = [{'from': 'n1', 'to': 'n2'}, {'from': 'n2', 'to': 'n3'}, {'from': 'n3', 'to': 'n4'}, {'from': 'n4', 'to': 'n1'}]
        self.assertEqual(errors({'nodes': square, 'edges': ring}, '1 2 3 4'), [])


class ItemFigureTests(unittest.TestCase):
    ## @brief 검증용 최소 단답 진단 문항
    def item(self, **changes):
        return {'id': 'shortest-path-d9', 'concept': 'shortest-path', 'purpose': 'diagnosis', 'objective': 0,
                'format': 'short_exact', 'question': QUESTION, 'answer': '5', 'rationale': '1→3→2가 5다.', **changes}

    # 검증 목적: figure가 없는 기존 문항이 그대로 읽히고, 도식이 있는 문항은 검사를 통과할 때만 읽힘
    def test_figure_is_optional_and_checked(self):
        self.assertIsNone(Item.model_validate(self.item()).figure)
        self.assertIsNotNone(Item.model_validate(self.item(figure=graph())).figure)
        nodes = graph()['nodes']
        leaked = graph(nodes=[*nodes, {'id': 'd', 'label': '5', 'shape': 'text', 'x': 4, 'y': 1}])
        with self.assertRaisesRegex(ValidationError, '지문'):
            Item.model_validate(self.item(figure=leaked))


if __name__ == '__main__':
    unittest.main()
