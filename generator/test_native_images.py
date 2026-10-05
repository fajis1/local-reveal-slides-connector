"""Regression coverage for editable Slides.com citation image blocks."""
import unittest

from lxml import html

from slides_api import prepare_payload
from validate_saved_slides import validate_saved_slides


ASSET = 'https://images.example.test/citation.png'


def payload():
    return prepare_payload({
        'title': 'Native image fixture', 'width': 1920, 'height': 1080,
        'slides': [{
            'id': 'citation-fixture-build-1', 'background-color': '#212121', 'notes': '',
            'blocks': [{
                'type': 'image', 'class': 'citation-img-1',
                'x': 160, 'y': 60, 'width': 1600, 'height': 900,
                'value': ASSET, 'natural-width': 2400, 'natural-height': 1350,
            }],
        }],
    })


class NativeImageTests(unittest.TestCase):
    def test_outgoing_image_has_native_editor_metadata(self):
        prepared = payload()
        root = html.fromstring(prepared['slides'][0]['html'])
        block = root.xpath('.//*[@data-block-type="image"]')[0]
        image = block.xpath('.//img')[0]
        self.assertEqual(image.get('src'), ASSET)
        self.assertEqual(image.get('data-src'), ASSET)
        self.assertEqual(image.get('data-natural-width'), '2400')
        self.assertEqual(image.get('data-natural-height'), '1350')
        self.assertEqual(len(block.xpath('./div[@class="sl-block-style"]/div[@class="sl-block-content"]')), 1)

    def test_saved_native_image_passes_editor_validation(self):
        prepared = payload()
        result = validate_saved_slides(prepared, {'deck_html': prepared['slides'][0]['html']})
        self.assertTrue(result['passed'], result['errors'])
        self.assertEqual(result['imageBlocks'], 1)
        self.assertEqual(result['nativeEditorImages'], 1)

    def test_lazy_only_placeholder_fails_editor_validation(self):
        prepared = payload()
        broken = prepared['slides'][0]['html'].replace(f' src="{ASSET}"', '')
        broken = broken.replace(' data-natural-width="2400"', '')
        broken = broken.replace(' data-natural-height="1350"', '')
        result = validate_saved_slides(prepared, {'deck_html': broken})
        self.assertFalse(result['passed'])
        self.assertEqual(result['nativeEditorImages'], 0)
        self.assertTrue(any('missing src' in error for error in result['errors']))
        self.assertTrue(any('data-natural-width' in error for error in result['errors']))


if __name__ == '__main__':
    unittest.main()
