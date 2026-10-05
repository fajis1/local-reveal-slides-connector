"""Regression tests for target-native typography and reference/content widths."""
import json
from pathlib import Path
import unittest
from width_policy import apply_content_width_policy, preserve_horizontal_anchor
from slides_native import convert_design_font_to_slides_percent


class WidthPolicyTests(unittest.TestCase):
    def test_reference_preserves_deliberate_long_strip(self):
        frame=[100,200,1765,66]
        self.assertEqual(apply_content_width_policy(frame,[953],{'mode':'reference'})[0],frame)

    def test_horizontal_anchors(self):
        frame=[100,200,1000,80]
        self.assertEqual(preserve_horizontal_anchor(frame,500,'left'),[100,200,500,80])
        self.assertEqual(preserve_horizontal_anchor(frame,500,'right'),[600,200,500,80])
        self.assertEqual(preserve_horizontal_anchor(frame,500,'center'),[350,200,500,80])

    def test_content_and_clamping(self):
        frame=[100,200,1000,80]
        policy={'mode':'content','paddingLeft':12,'paddingRight':12,'anchor':'left'}
        self.assertEqual(apply_content_width_policy(frame,[400,500],policy)[0][2],524)
        policy.update(mode='content-clamped',minWidth=600,maxWidth=900)
        self.assertEqual(apply_content_width_policy(frame,[400,500],policy)[0][2],600)

    def test_width_policy_is_not_used_for_focus_scaling(self):
        policy={'mode':'content-clamped','minWidth':320,'paddingLeft':12,'paddingRight':12}
        frame,_=apply_content_width_policy([100,200,800,60],[400],policy,1)
        self.assertEqual(frame[2],424)

    def test_native_percentage_calibrated_base(self):
        self.assertAlmostEqual(convert_design_font_to_slides_percent(60),200)
        self.assertAlmostEqual(convert_design_font_to_slides_percent(79.25),264.166667,places=5)

    def test_do_not_clip_when_design_padding_is_too_large(self):
        frame,info=apply_content_width_policy([0,200,1000,80],[995],{'mode':'content-clamped'})
        self.assertEqual(frame[2],1000)
        self.assertAlmostEqual(info['paddingLeft']+info['paddingRight'],5)


def verify_generated(before,after):
    baseline=json.loads((before/'slides.json').read_text())
    current=json.loads((after/'slides.json').read_text())
    assert len(current)==18 and sum(len(s['buildStates']) for s in current)==85
    # The user-approved readability policy supersedes old passage dimensions.
    # Static labels, source wording, palettes, state count and coherent wrapping
    # still have to survive; the former four opt-ins are no longer the scope.
    design=json.loads(Path(__file__).with_name('layout-design.json').read_text())
    for old,new in zip(baseline[:8],current[:8]):
        for a,b in zip(old['buildStates'],new['buildStates']):
            assert [r for r in a['runs'] if r['boxIndex'] is None]==[r for r in b['runs'] if r['boxIndex'] is None],new['id']+' static heading drift'
            assert [(s['boxIndex'],s['fill'],s['opacity']) for s in a['shapes']]==[(s['boxIndex'],s['fill'],s['opacity']) for s in b['shapes']],new['id']+' palette drift'
            assert all(c['readability']['wholeSlide'] for c in b['components'])
    for name,count,body_count in [('slides-com-editable-18.json',18,85),('slides-com-builds.json',85,407)]:
        data=json.loads((after/name).read_text())
        assert len(data['slides'])==count
        blocks=[b for s in data['slides'] for b in s['blocks']]
        passages=[b for b in blocks if b['role']=='passage']
        assert len(passages)==body_count
        assert sum(b.get('auto-fit-text') is True for b in blocks)==body_count
        assert all(b['type']=='text' for b in blocks)
        assert all(b['font-size'].endswith('%') and b['color'] for b in blocks)
        assert all(b['text-layout']=='fixed' for b in passages)
    before_frame=baseline[-1]['buildStates'][1]['components'][1]['frame']
    after_frame=current[-1]['buildStates'][1]['components'][1]['frame']
    # A larger requested font may need a wider strip. Compare width per font
    # unit so growing the text is allowed without introducing empty extensions.
    before_size=next(r['size'] for r in baseline[-1]['buildStates'][1]['runs'] if r['boxIndex']==1)
    after_size=next(r['size'] for r in current[-1]['buildStates'][1]['runs'] if r['boxIndex']==1)
    assert after_frame[2]/after_size<=before_frame[2]/before_size+.001
    assert abs(before_frame[0]+before_frame[2]-after_frame[0]-after_frame[2])<.001
    # Exports format the JSON; compare exact string values, not indentation.
    assert json.loads((before/'source.json').read_text())==json.loads((after/'source.json').read_text())
    # Every canonical passage wrap remains identical across its build states.
    builds=json.loads((after/'slides-com-builds.json').read_text())
    wraps={}
    for slide in builds['slides']:
        logical=slide['id'].rsplit('-build-',1)[0]
        for block in slide['blocks']:
            if block['role']!='passage': continue
            native=block['native-typography']
            key=(logical,block['class'].rsplit('-',1)[1])
            if key in wraps: assert wraps[key]==native['lines'],'Native wrap drift: '+str(key)
            wraps[key]=native['lines']
            if block.get('focus-sizing'):
                top,bottom=block['focus-sizing']['availableRow']
                assert native['frame'][1]>=top-.001 and native['frame'][1]+native['frame'][3]<=bottom+.001
            else:
                assert native['frame'][3]<=native['designFrame'][3]+.001
            if native['background'] and native['widthPolicy']['mode']!='reference':
                width=max(native['lineWidths'])+native['insets']['left']+native['insets']['right']+native['allowance']
                assert abs(native['frame'][2]-width)<.001
    assert len(wraps)==85
    # Both target fonts must fit the joint row allocations without overlap.
    for slide in builds['slides']:
        bodies=[b for b in slide['blocks'] if b['role']=='passage']
        for left,right in zip(bodies,bodies[1:]):
            a=left['native-typography']['frame'];b=right['native-typography']['frame']
            assert a[1]+a[3]<=b[1]+.001,'Native passage overlap'
        for block in bodies:
            native=block['native-typography']
            assert native['size']<=block['focus-sizing']['preferredFontSize']+.001
    print(json.dumps({'referencePalettesCompared':8,'wholeSlideSizingPassages':len(wraps),'sourceContentUnchanged':True,
                      'page82Before':before_frame,'page82After':after_frame,
                      'autoFitEditable':85,'autoFitBuilds':407,'shapeBlocks':0},indent=2))


if __name__=='__main__':
    import sys
    if len(sys.argv)==3: verify_generated(Path(sys.argv[1]),Path(sys.argv[2]))
    else: unittest.main()
