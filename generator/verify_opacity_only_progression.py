"""Assert that every progressive logical slide changes only passage opacity."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import sys


def without_opacity(value):
    if isinstance(value, dict):
        return {key: without_opacity(item) for key, item in value.items() if key != 'opacity'}
    if isinstance(value, list):
        return [without_opacity(item) for item in value]
    return value


def boxes_in_state(state):
    result = {}
    for index in {item['boxIndex'] for item in state['components']}:
        result[index] = {
            'component': next(item for item in state['components'] if item['boxIndex'] == index),
            'shapes': [item for item in state['shapes'] if item.get('boxIndex') == index],
            'runs': [item for item in state['runs'] if item.get('boxIndex') == index],
        }
    return result


def verify_reveal(deck):
    examples = []
    for logical in deck:
        states = logical['buildStates']
        if len(states) < 3:
            continue
        baseline = boxes_in_state(states[0])
        for state in states[1:]:
            current = boxes_in_state(state)
            assert baseline.keys() == current.keys(), logical['id'] + ': box ordering changed'
            for index in baseline:
                assert without_opacity(baseline[index]) == without_opacity(current[index]), (
                    f"{logical['id']} box {index}: non-opacity Reveal property drift")
        for index in baseline:
            opacities = [next(item for item in state['components'] if item['boxIndex'] == index)['opacity'] for state in states]
            assert set(opacities) == {0.5, 1}, f"{logical['id']} box {index}: expected 0.5/1 opacity"
        examples.append(logical['id'])
    assert examples, 'Expected at least one progressive logical slide'
    return examples


def verify_define(definition):
    grouped = {}
    for slide in definition['slides']:
        logical = slide['id'].rsplit('-build-', 1)[0]
        for block in slide['blocks']:
            if block.get('role') != 'passage':
                continue
            grouped.setdefault((logical, block['class']), []).append(block)
    for key, blocks in grouped.items():
        if len(blocks) < 3:
            continue
        baseline = without_opacity(blocks[0])
        assert all(without_opacity(block) == baseline for block in blocks[1:]), key + (' Define block drift',)
        assert {block['opacity'] for block in blocks} == {0.5, 1}, key + (' opacity mismatch',)


def normalized_html(markup):
    markup = re.sub(r'\sdata-block-id="[^"]+"', '', markup)
    markup = re.sub(r'opacity:(?:0\.5|1);', '', markup)
    return markup


def verify_rest(definition):
    from lxml import html
    from slides_api import prepare_payload
    payload = prepare_payload(definition)
    passage_classes = {
        block['class'] for slide in definition['slides'] for block in slide['blocks']
        if block.get('role') == 'passage'
    }
    grouped = {}
    for source, prepared in zip(definition['slides'], payload['slides']):
        logical = source['id'].rsplit('-build-', 1)[0]
        root = html.fromstring(prepared['html'])
        for block in root.xpath('.//*[@data-generator-class]'):
            class_name = block.get('data-generator-class')
            if class_name not in passage_classes:
                continue
            grouped.setdefault((logical, class_name), []).append(html.tostring(block, encoding='unicode'))
    for key, blocks in grouped.items():
        if len(blocks) < 3:
            continue
        baseline = normalized_html(blocks[0])
        assert all(normalized_html(block) == baseline for block in blocks[1:]), key + (' REST HTML drift',)
        opacities = set(re.findall(r'opacity:([^;]+);', ''.join(blocks)))
        assert opacities == {'0.5', '1'}, key + (' REST opacity mismatch',)


def verify(output: Path):
    deck = json.loads((output / 'slides.json').read_text())
    definition = json.loads((output / 'slides-com-builds.json').read_text())
    examples = verify_reveal(deck)
    verify_define(definition)
    verify_rest(definition)
    return {'logicalSlidesChecked': examples, 'opacityValues': [0.5, 1], 'nonOpacityDrift': False}


if __name__ == '__main__':
    result = verify(Path(sys.argv[1]))
    print(json.dumps(result, indent=2))
