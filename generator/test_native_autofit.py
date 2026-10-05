"""Fixtures/regressions for native initial fit, anchors, BRs and Auto-fit CSS."""
from copy import deepcopy
import unittest
from lxml import html
from slides_native import NativeTypography, serialize_rest_text_block, serialize_define_text_block
from validate_saved_slides import css, validate_autofit_block


def fixture_blocks():
    base={'type':'text','role':'passage','class':'box-fixture','x':120,'y':180,
          'width':1450,'height':180,'font-family':'DINCondensed-Bold',
          'font-size-px':60,'line-height':1.25,'color':'#000000',
          'background-color':'#ffce55','text-insets':dict.fromkeys(('top','right','bottom','left'),4),
          'width-policy':{'mode':'reference','anchor':'left'}}
    definitions=[
        ('single-line',{'value':'...BUT BY THE POWER OF AN INDESTRUCTIBLE LIFE.'}),
        ('natural-wrap',{'value':'BUT WHOEVER WOULD BE GREAT AMONG YOU MUST BE YOUR SERVANT, AND WHOEVER WOULD BE FIRST AMONG YOU MUST BE SLAVE OF ALL.',
                         'width':650,'height':340,'wrap-mode':'natural'}),
        ('explicit-br',{'value':'YET YOU HAVE MADE HIM A LITTLE LOWER THAN THE HEAVENLY BEINGS<br>AND CROWNED HIM WITH GLORY AND HONOR.',
                       'width':1500,'height':250}),
        ('inline-colors',{'value':'LET US MAKE MAN IN <span style="color:#ff3300">OUR</span> IMAGE, AFTER <span style="color:#ff3300">OUR</span> LIKENESS.'}),
        ('left-anchor',{'value':'...FOR YOU ARE DUST, AND TO DUST YOU SHALL RETURN.',
                        'width-policy':{'mode':'reference','anchor':'left'}}),
        ('right-anchor',{'value':'...FOR YOU ARE DUST, AND TO DUST YOU SHALL RETURN.',
                         'width-policy':{'mode':'reference','anchor':'right'}}),
        ('center-anchor',{'value':'...FOR YOU ARE DUST, AND TO DUST YOU SHALL RETURN.',
                          'width-policy':{'mode':'reference','anchor':'center'}}),
    ]
    return [(name,{**deepcopy(base),**changes,'class':'box-fixture-'+name}) for name,changes in definitions]


class NativeAutoFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.typography=NativeTypography()

    def check_block(self, block):
        native=self.typography.fit(block)
        definition=serialize_define_text_block(block,native)
        self.assertEqual(definition['text-layout'],'fixed')
        self.assertIs(definition['auto-fit-text'],True)
        self.assertTrue(definition['font-size'].endswith('%'))
        markup=serialize_rest_text_block(block,native,'fixture')
        root=html.fromstring(markup)
        self.assertEqual(root.get('data-block-type'),'text')
        self.assertEqual(root.get('data-text-layout'),'fixed')
        self.assertEqual(root.get('data-auto-fit-text'),'true')
        self.assertEqual(validate_autofit_block(root),[])
        self.assertEqual(len(root.xpath('.//*[@data-block-type="shape"]')),0)
        content=root.xpath('.//div[@class="sl-block-content"]')[0]
        self.assertEqual(css(content)['background-color'],block['background-color'])
        self.assertNotIn('width',css(content));self.assertNotIn('height',css(content))
        self.assertTrue(all('white-space' not in css(n) for n in root.iter()))
        self.assertAlmostEqual(native['frame'][3],native['textHeight']+native['insets']['top']+native['insets']['bottom']+native['allowance'])
        self.assertLessEqual(native['frame'][3],block['height']+.01)
        return native,root

    def test_seven_fixture_structures_and_initial_metrics(self):
        for name,block in fixture_blocks():
            with self.subTest(name=name):
                native,root=self.check_block(block)
                self.assertAlmostEqual(native['frame'][2],max(native['lineWidths'])+native['insets']['left']+native['insets']['right']+native['allowance'])
                if name=='natural-wrap':
                    self.assertGreater(len(native['lines']),1);self.assertEqual(len(root.xpath('.//br')),0)
                if name=='explicit-br': self.assertEqual(len(root.xpath('.//br')),1)
                if name=='inline-colors': self.assertEqual(len(root.xpath('.//span[@style]')),2)

    def test_anchor_edges_are_preserved(self):
        for name,block in fixture_blocks()[4:]:
            native,_=self.check_block(block);x,y,w,h=native['frame']
            if name=='left-anchor': self.assertEqual(x,block['x'])
            if name=='right-anchor': self.assertAlmostEqual(x+w,block['x']+block['width'])
            if name=='center-anchor': self.assertAlmostEqual(x+w/2,block['x']+block['width']/2)

    def test_historical_width_requires_explicit_native_override(self):
        block=fixture_blocks()[0][1]
        fitted,_=self.check_block(block)
        self.assertLess(fitted['frame'][2],block['width']*.8)
        block['native-width-policy']={'mode':'reference'}
        fitted,_=self.check_block(block)
        self.assertEqual(fitted['frame'][2],block['width'])

    def test_forbidden_css_is_rejected(self):
        block=fixture_blocks()[0][1]
        for forbidden in ['width:100%','height:100%','white-space:pre',
                          'width : 100% !important','white-space : PRE !important']:
            native=self.typography.fit(block)
            root=html.fromstring(serialize_rest_text_block(block,native,'bad-fixture'))
            content=root.xpath('.//div[@class="sl-block-content"]')[0]
            node=content.xpath('.//p')[0] if forbidden.startswith('white') else content
            node.set('style',node.get('style','')+forbidden+';')
            with self.subTest(forbidden=forbidden): self.assertTrue(validate_autofit_block(root))

    def test_native_wrapper_is_required(self):
        block=fixture_blocks()[0][1];native=self.typography.fit(block)
        root=html.fromstring(serialize_rest_text_block(block,native,'fixture'))
        root.xpath('.//div[@class="sl-block-style"]')[0].set('class','wrong-wrapper')
        self.assertIn('Native style wrapper missing',validate_autofit_block(root))

    def test_content_clamp_is_applied_before_fitting(self):
        block=fixture_blocks()[0][1]
        block['native-width-policy']={'mode':'content-clamped','maxWidth':500}
        native,_=self.check_block(block)
        self.assertLessEqual(native['frame'][2],500)
        self.assertLess(native['size'],block['font-size-px'])
        self.assertLessEqual(max(native['lineWidths'])+native['insets']['left']+native['insets']['right']+native['allowance'],500)


if __name__=='__main__': unittest.main()
