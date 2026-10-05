"""Compare an actually saved Slides.com deck against its expected native payload."""
import html
import re
from urllib.parse import urlsplit
from lxml import html as dom


def css(node):
    return {key.strip().lower():value.strip() for part in node.get('style','').split(';') if ':' in part
            for key,value in [part.split(':',1)]}


def color(value):
    value=value.strip().lower()
    if value.startswith('#'):
        value=value[1:]
        if len(value)==3: value=''.join(c*2 for c in value)
        return tuple(int(value[i:i+2],16) for i in (0,2,4))
    if value.startswith('rgb'):
        return tuple(round(float(v)) for v in re.findall(r'[\d.]+',value)[:3])
    return value


def lines(node):
    text=dom.tostring(node,encoding='unicode')
    text=re.sub(r'<br\s*/?>','\n',text)
    text=html.unescape(re.sub(r'<[^>]*>','',text))
    return [' '.join(line.split()) for line in text.splitlines() if line.strip()]


def validate_autofit_block(block):
    """Reject CSS proven to break native shrink/grow before and after saving."""
    if block.get('data-auto-fit-text')!='true': return []
    errors=[]
    if block.get('data-text-layout')!='fixed': errors.append('Auto-fit layout must be fixed')
    content=block.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," sl-block-content ")]')
    if len(content)!=1: return errors+['Auto-fit requires exactly one content element']
    if 'sl-block-style' not in content[0].getparent().get('class','').split():
        errors.append('Native style wrapper missing')
    for prop in ['width','height']:
        if re.sub(r'\s|!important','',css(content[0]).get(prop,''),flags=re.I)=='100%':
            errors.append('Forbidden content '+prop+':100%')
    for node in block.iter():
        if re.sub(r'\s|!important','',css(node).get('white-space',''),flags=re.I).lower()=='pre':
            errors.append('Forbidden white-space:pre')
    return errors


def image_url(image):
    """An editor-native image must retain both the eager and lazy URL forms."""
    src = image.get('src')
    data_src = image.get('data-src')
    return src or data_src, src, data_src


def valid_https_url(value):
    parsed = urlsplit(value or '')
    return parsed.scheme == 'https' and bool(parsed.netloc)


def positive_dimension(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def validate_image_block(expected, actual, label):
    """Validate editor readiness, not merely presentation-mode lazy loading."""
    errors = []
    expected_images = expected.xpath('.//img')
    actual_images = actual.xpath('.//img')
    if len(expected_images) != 1:
        return [label + ': expected image block must contain exactly one img']
    if len(actual_images) != 1:
        return [label + ': saved image block must contain exactly one img']
    expected_url, _, _ = image_url(expected_images[0])
    image = actual_images[0]
    actual_url, src, data_src = image_url(image)
    if not valid_https_url(expected_url):
        errors.append(label + ': expected image URL is not absolute HTTPS')
    if not valid_https_url(actual_url):
        errors.append(label + ': saved image URL is not absolute HTTPS')
    if not src:
        errors.append(label + ': saved image is missing src (editor placeholder risk)')
    if not data_src:
        errors.append(label + ': saved image is missing data-src')
    if src and data_src and src != data_src:
        errors.append(label + ': saved src and data-src differ')
    if expected_url != actual_url or (src and expected_url != src) or (data_src and expected_url != data_src):
        errors.append(label + ': saved image URL differs from expected citation asset')
    for attribute in ('data-natural-width', 'data-natural-height'):
        if not positive_dimension(image.get(attribute)):
            errors.append(label + ': saved image has invalid ' + attribute)
    content = actual.xpath('./div[contains(concat(" ",normalize-space(@class)," ")," sl-block-style ")]/div[contains(concat(" ",normalize-space(@class)," ")," sl-block-content ")]')
    if len(content) != 1:
        errors.append(label + ': saved image lacks native style/content wrappers')
    geometry = css(actual)
    for key in ('left', 'top', 'width', 'height'):
        if not positive_dimension(geometry.get(key, '0').removesuffix('px')) and key in ('width', 'height'):
            errors.append(label + ': saved image has invalid ' + key)
        if key in ('left', 'top') and key not in geometry:
            errors.append(label + ': saved image is missing ' + key)
    return errors


def validate_saved_slides(payload, saved):
    actual=dom.fromstring('<main>'+saved['deck_html']+'</main>')
    errors=[]; passage_count=0; text_count=0; image_count=0; backgrounds=0; inline_colors=0
    sections=actual.xpath('//section')
    if len(sections)!=len(payload['slides']): errors.append('Saved slide count differs')
    shapes=actual.xpath('//*[@data-block-type="shape"]')
    if shapes: errors.append('Saved deck contains separate shape blocks')
    for index,slide in enumerate(payload['slides']):
        expected=dom.fromstring(slide['html'])
        if index>=len(sections): break
        section=sections[index]
        found={b.get('data-block-id'):b for b in section.xpath('.//*[@data-block-type="text"]')}
        expected_blocks=expected.xpath('.//*[@data-block-type="text"]')
        if len(found)!=len(expected_blocks): errors.append(f'Slide {index+1}: block count changed')
        for block in expected_blocks:
            identity=block.get('data-block-id'); label=f'slide {index+1} block {identity}'
            target=found.get(identity)
            if target is None: errors.append(label+': missing native text block'); continue
            text_count+=1
            content=block.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," sl-block-content ")]')[0]
            saved_content=target.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," sl-block-content ")]')[0]
            if target.get('data-text-layout')!='fixed': errors.append(label+': layout not fixed')
            body=block.get('data-auto-fit-text')=='true'
            if body:
                passage_count+=1
                if target.get('data-auto-fit-text')!='true': errors.append(label+': native auto-fit missing')
                errors.extend(label+': '+error for error in validate_autofit_block(block))
                errors.extend(label+': saved '+error for error in validate_autofit_block(target))
            elif target.get('data-auto-fit-text')=='true': errors.append(label+': label auto-fit unexpectedly enabled')
            ec,ac=css(content),css(saved_content)
            for k in ['font-size','font-family','font-weight','line-height','padding','color','background-color']:
                if k not in ec: continue
                if k not in ac: errors.append(label+': missing native '+k); continue
                if k=='font-size' and not ac[k].endswith('%'):
                    errors.append(label+': native percentage font-size unit changed')
                if 'color' in k:
                    if color(ec[k])!=color(ac[k]): errors.append(label+': changed '+k)
                elif k in ['font-size','line-height','font-weight']:
                    if abs(float(re.findall(r'[\d.]+',ec[k])[0])-float(re.findall(r'[\d.]+',ac[k])[0]))>.02:
                        errors.append(label+': changed '+k)
                elif k=='font-family':
                    if ec[k].replace('"','').replace("'",'').replace(' ','').lower()!=ac[k].replace('"','').replace("'",'').replace(' ','').lower():
                        errors.append(label+': changed font family')
                elif k=='padding':
                    before=[float(v) for v in re.findall(r'[\d.]+',ec[k])]
                    after=[float(v) for v in re.findall(r'[\d.]+',ac[k])]
                    if len(after)==1: after*=4
                    elif len(after)==2: after*=2
                    elif len(after)==3: after.append(after[1])
                    if len(before)!=len(after) or any(abs(a-b)>.02 for a,b in zip(before,after)): errors.append(label+': changed padding')
            if 'background-color' in ec: backgrounds+=1
            if lines(content)!=lines(saved_content): errors.append(label+': text or explicit line breaks changed')
            if len(content.xpath('.//br'))!=len(saved_content.xpath('.//br')): errors.append(label+': explicit BR count changed')
            for key in ['left','top','width','height']:
                try:
                    if abs(float(css(block)[key].replace('px',''))-float(css(target)[key].replace('px',''))) > 1.01:
                        errors.append(label+': changed '+key)
                except (ValueError,KeyError): errors.append(label+': invalid geometry '+key)
            expected_spans=[(s.text_content(),color(css(s)['color'])) for s in content.xpath('.//span[@style]') if 'color' in css(s)]
            actual_spans=[(s.text_content(),color(css(s)['color'])) for s in saved_content.xpath('.//span[@style]') if 'color' in css(s)]
            if expected_spans!=actual_spans: errors.append(label+': inline word colors changed')
            inline_colors+=len(expected_spans)
        found_images={b.get('data-block-id'):b for b in section.xpath('.//*[@data-block-type="image"]')}
        expected_images=expected.xpath('.//*[@data-block-type="image"]')
        if len(found_images)!=len(expected_images): errors.append(f'Slide {index+1}: image block count changed')
        for block in expected_images:
            identity=block.get('data-block-id'); label=f'Slide {index+1} image {identity}'
            target=found_images.get(identity)
            if target is None:
                errors.append(label+': missing native image block')
                continue
            image_count+=1
            errors.extend(validate_image_block(block,target,label))
    return {'slides':len(sections),'textBlocks':text_count,'passageBlocks':passage_count,
            'nativeAutoFit':len(actual.xpath('//*[@data-block-type="text"][@data-auto-fit-text="true"]')),
            'backgroundTextBlocks':backgrounds,'inlineColorSpans':inline_colors,
            'imageBlocks':image_count,
            'nativeEditorImages':image_count if not any('image ' in error.lower() for error in errors) else 0,
            'shapeBlocks':len(shapes),'errors':errors,'passed':not errors}
