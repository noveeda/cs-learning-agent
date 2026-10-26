# 파일 역할: 모델 라벨의 XML 이스케이프, 잘못된 그래프의 파일 생성 차단, 모델에 보내는 도구 스키마 형태를 검증
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from pydantic import ValidationError
from serving import agent, context
from serving.tools.diagram import TOOL, create_diagram


class DiagramTests(unittest.TestCase):
    def setUp(self):
        self.data = {'title': '페이지 매핑', 'nodes': [
            {'id': 'page', 'label': '<script>alert(1)</script>', 'column': 0, 'row': 0},
            {'id': 'frame', 'label': '프레임 3', 'column': 1, 'row': 0}],
            'edges': [{'source': 'page', 'target': 'frame'}]}

    # 검증 목적: 모델 텍스트를 SVG 코드로 해석하지 않고 지정 디렉터리에 저장하는지 확인
    def test_svg_escapes_model_text_and_stays_in_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            result = create_diagram(json.dumps(self.data), Path(folder))
            path = Path(result['path'])
            self.assertEqual(path.parent, Path(folder).resolve())
            root = ElementTree.parse(path).getroot()
            self.assertFalse(root.findall('.//{http://www.w3.org/2000/svg}script'))
            self.assertIn('&lt;script&gt;', path.read_text())

    # 검증 목적: 유효하지 않은 그래프는 결과 파일을 만들지 않는지 확인
    def test_invalid_graph_creates_no_artifact(self):
        for mutation in ('missing_target', 'duplicate_id', 'overlap', 'extra_field'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                data = json.loads(json.dumps(self.data))
                if mutation == 'missing_target':
                    data['edges'][0]['target'] = 'missing'
                elif mutation == 'duplicate_id':
                    data['nodes'][1]['id'] = 'page'
                elif mutation == 'overlap':
                    data['nodes'][1]['column'] = 0
                else:
                    data['code'] = 'execute me'
                with self.assertRaises(ValidationError):
                    create_diagram(json.dumps(data), Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])

    # 검증 목적: 도구 스키마에 $ref·$defs가 없어 노드·간선 필드가 그대로 보이고, 격자 범위 같은 제약이 description 문장으로도 들어가는지 확인
    # - Gemma 4 채팅 템플릿은 $defs와 제약 키워드를 렌더링하지 않아, 펼치지 않으면 모델이 column·row를 몰라 도식 호출이 실패함
    def test_tool_schema_is_inlined_with_described_constraints(self):
        parameters = TOOL['function']['parameters']
        dumped = json.dumps(parameters)
        self.assertNotIn('$ref', dumped)
        self.assertNotIn('$defs', dumped)
        node = parameters['properties']['nodes']['items']['properties']
        self.assertEqual(node['column']['description'], '최솟값 0, 최댓값 3')
        self.assertEqual(node['row']['description'], '최솟값 0, 최댓값 7')
        self.assertEqual(node['id']['description'], '정규식 ^[A-Za-z][A-Za-z0-9_]{0,31}$')
        self.assertEqual(node['label']['description'], '최소 길이 1, 최대 길이 32')
        self.assertEqual(parameters['properties']['nodes']['description'], '최소 개수 1, 최대 개수 16')
        self.assertEqual(set(parameters['properties']['edges']['items']['properties']), {'source', 'target'})
        # 제약 키워드 자체도 남겨 두어 템플릿이 렌더링하는 모델에는 그대로 전달
        self.assertEqual((node['column']['minimum'], node['column']['maximum']), (0, 3))
        # 채팅 요청과 /tokenize 토큰 계산이 같은 도구 정의를 써야 실제 입력 토큰 수와 계산 값이 맞음
        self.assertIs(agent.TOOL, TOOL)
        self.assertIs(context.TOOL, TOOL)


if __name__ == '__main__':
    unittest.main()
