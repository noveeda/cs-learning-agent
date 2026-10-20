import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from pydantic import ValidationError
from serving.tools.diagram import create_diagram


class DiagramTests(unittest.TestCase):
    def setUp(self):
        self.data = {'title': '페이지 매핑', 'nodes': [
            {'id': 'page', 'label': '<script>alert(1)</script>', 'column': 0, 'row': 0},
            {'id': 'frame', 'label': '프레임 3', 'column': 1, 'row': 0}],
            'edges': [{'source': 'page', 'target': 'frame'}]}

    def test_svg_escapes_model_text_and_stays_in_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            result = create_diagram(json.dumps(self.data), Path(folder))
            path = Path(result['path'])
            self.assertEqual(path.parent, Path(folder).resolve())
            root = ElementTree.parse(path).getroot()
            self.assertFalse(root.findall('.//{http://www.w3.org/2000/svg}script'))
            self.assertIn('&lt;script&gt;', path.read_text())

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


if __name__ == '__main__':
    unittest.main()
