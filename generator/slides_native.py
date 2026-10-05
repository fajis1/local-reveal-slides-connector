"""Measured native initial layout and CSS compatible with bidirectional Auto-fit."""
import html
import json
from pathlib import Path
import re
from fontTools.ttLib import TTFont
from width_policy import apply_content_width_policy


def native_configuration():
    return json.loads(Path(__file__).with_name('slides-native.json').read_text())


def convert_design_font_to_slides_percent(size, config=None):
    config = config or native_configuration()
    return round(size / config['baseFontSizePx'] * 100, 6)


class NativeTypography:
    def __init__(self):
        self.config = native_configuration()
        self.fonts = {}
        for name, spec in self.config['fonts'].items():
            path = Path(spec['path'])
            if not path.exists() and name == 'alternate':
                path = next(Path('/usr/share/fonts').rglob('LiberationSans-Bold.ttf'))
            self.fonts[name] = TTFont(path)

    def fit(self, block):
        name = 'alternate' if block.get('font-family') == 'DINAlternate-Bold' else 'condensed'
        spec = self.config['fonts'][name]
        face = self.fonts[name]
        em = face['head'].unitsPerEm
        cmap = face.getBestCmap()
        lines = [html.unescape(re.sub(r'<[^>]*>', '', line)) for line in re.split(r'<br\s*/?>', block['value'])]
        advance = lambda text: sum(face['hmtx'].metrics[cmap[ord(c)]][0] for c in text)/em
        frame = list(block.get('design-frame') or [block[k] for k in ('x','y','width','height')])
        design_frame = list(frame)
        passage = block.get('role')=='passage' or block['class'].startswith('box-')
        policy = dict(block.get('width-policy', {'mode':'reference'}))
        # A measured reference profile is an envelope, not an instruction to
        # retain its old width in a different font/content. Exact native widths
        # require an explicit native-width-policy override in layout/styles.
        if passage:
            policy = {**self.config['passageWidthPolicy'], 'anchor':(block.get('focus-sizing') or {}).get('anchor',policy.get('anchor','left')),
                      **block.get('native-width-policy',{})}
        scale = block.get('width-scale',1)
        focus_sizing=block.get('focus-sizing') if passage else None
        if focus_sizing:
            # Native font leading differs from DIN. Use the same available row
            # to fit the initial native text, rather than imposing DIN's shorter
            # line box and shrinking a passage despite free vertical space.
            row_top,row_bottom=focus_sizing['availableRow']
            frame[3]=(row_bottom-row_top)*(1 if focus_sizing.get('wholeSlide') else scale)
        insets = dict(block.get('text-insets',dict.fromkeys(('top','right','bottom','left'),0)))
        if policy.get('mode') != 'reference':
            insets['left'] = policy.get('paddingLeft',12)*scale
            insets['right'] = policy.get('paddingRight',12)*scale
        insets['top'] = max(3,insets['top'])
        insets['bottom'] = max(3,insets['bottom'])
        if focus_sizing and focus_sizing.get('wholeSlide'):
            # The joint allocator reserves the actual native line height too.
            # Use its scaled padding, rather than an SVG baseline conversion.
            insets['top']=insets['bottom']=max(3,focus_sizing['paddingVertical']*scale)
        heading_policy=block.get('heading-policy') if block.get('label-kind')=='heading' else None
        reference_policy=block.get('reference-policy') if block.get('label-kind')=='reference' else None
        label_policy=heading_policy or reference_policy
        if label_policy:
            # DIN and the native font have different baselines. Fit the native
            # label in its shared frame, then align its natural line box below.
            insets['top']=3
            insets['bottom']=3
        leading = max(block.get('line-height',1), (face['hhea'].ascent-face['hhea'].descent)/em)
        allowance = self.config['initialFitAllowancePx']
        # A configured clamp is part of the fitting envelope, not merely a
        # rectangle trim after font fitting; honor it before measuring fit.
        fitting_width=min(frame[2],policy.get('maxWidth',frame[2])) if policy.get('mode')=='content-clamped' else frame[2]
        width = fitting_width-insets['left']-insets['right']-allowance
        height = frame[3]-insets['top']-insets['bottom']-allowance
        if block.get('wrap-mode')=='natural':
            if len(lines)!=1:
                raise ValueError('Natural wrapping cannot replace an intentional explicit BR')
            text=lines[0]
            for tick in range(int(block['font-size-px']*4), int(self.config['minimumFontSizePx']*4)-1,-1):
                size=tick/4; selected=[]; line=''
                for word in text.split():
                    candidate=(line+' '+word).strip()
                    if line and advance(candidate)*size>width:
                        selected.append(line);line=word
                    else: line=candidate
                if line: selected.append(line)
                if max((advance(line)*size for line in selected),default=0)<=width and len(selected)*size*leading<=height:
                    lines=selected;break
            else: raise ValueError('Natural native text cannot fit: '+block['class'])
        else:
            advances = [advance(line) for line in lines]
            size = min(block['font-size-px'], width/max(advances,default=1), height/(len(lines)*leading))
        size = int(size*4)/4
        if size < self.config['minimumFontSizePx']:
            raise ValueError('Native editor text cannot fit: '+block['class'])
        advances = [advance(line) for line in lines]
        # The tiny allowance avoids fractional-pixel wrapping; it is included
        # in the outer frame, never by stretching the inner content element.
        line_widths=[a*size for a in advances]
        frame, adjustment = apply_content_width_policy(frame,[w+allowance for w in line_widths],policy,scale)
        if adjustment:
            insets['left']=adjustment['paddingLeft']; insets['right']=adjustment['paddingRight']
        text_height = len(lines)*leading*size
        if passage:
            frame[3] = text_height+insets['top']+insets['bottom']+allowance
            if focus_sizing:
                center=design_frame[1]+design_frame[3]/2
                frame[1]=max(row_top,min(center-frame[3]/2,row_bottom-frame[3]))
        elif label_policy:
            insets['top']=frame[3]-text_height-insets['bottom']
            if reference_policy:
                # Preserve the shared right edge with the actual native font,
                # whose narrower advances must not leave the reference at left.
                insets['left']=frame[2]-max(line_widths)-insets['right']-allowance
        return {'version':self.config['version'],'frame':frame,'designFrame':design_frame,
                'size':size,'percent':convert_design_font_to_slides_percent(size,self.config),
                'family':spec['family'],'weight':spec['weight'],'lineHeight':leading,
                'insets':insets,'lines':lines,'lineWidths':line_widths,'textHeight':text_height,
                'widthPolicy':policy,'widthAdjustment':adjustment,'allowance':allowance,
                'autoFit':passage,'align':'right' if reference_policy else 'left',
                'color':block.get('color','#ffffff'),'background':block.get('background-color')}


def serialize_define_text_block(block, native):
    """Documented native fields carry core styling without deck-level CSS."""
    output=dict(block)
    output.update(dict(zip(('x','y','width','height'),native['frame'])))
    output['font-size']=str(native['percent'])+'%'
    output['color']=native['color']
    output['align']=native.get('align','left')
    output['padding']=min(native['insets'].values())
    output['text-layout']='fixed'
    output['auto-fit-text']=native['autoFit']
    # Keep target metadata for the REST adapter; these are not Define API claims.
    output['native-typography']=native
    output['design-frame']=native['designFrame']
    return output


def serialize_rest_text_block(block, native, block_id):
    x,y,width,height=native['frame']
    inset=native['insets']
    padding=' '.join(str(inset[s])+'px' for s in ('top','right','bottom','left'))
    content=(f'font-family:\'{native["family"]}\',sans-serif;font-size:{native["percent"]}%;'
             f'font-weight:{native["weight"]};line-height:{native["lineHeight"]};'
             f'text-align:{native.get("align","left")};color:{native["color"]};box-sizing:border-box;'
             f'padding:{padding};z-index:1;')
    if native['background']:
        content+=f'background-color:{native["background"]};'
    auto=' data-auto-fit-text="true"' if native['autoFit'] else ''
    # Native Auto-fit increases and decreases the inherited percentage. Inner
    # 100% sizing and preformatted whitespace break that editor behavior.
    # Intentional canonical line breaks live in explicit BR elements.
    opacity=block.get('opacity',1)
    generator_class=html.escape(block.get('class',''), quote=True)
    return (f'<div class="sl-block" data-block-type="text" data-block-id="{block_id}" data-generator-class="{generator_class}" '
            f'data-text-layout="fixed"{auto} style="left:{x}px;top:{y}px;width:{width}px;height:{height}px;">'
            f'<div class="sl-block-style" style="z-index:1;transform:rotate(0deg);opacity:{opacity};">'
            f'<div class="sl-block-content" data-placeholder-tag="p" data-has-custom-html="true" style="{content}">'
            f'<p style="margin:0;line-height:inherit;">{block["value"]}</p></div></div></div>')
