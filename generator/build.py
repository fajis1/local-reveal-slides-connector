"""Generate a complete deck, offline HTML, and editable Slides.com definitions."""
import base64
import html
import json
from pathlib import Path
import re
import shutil
import sys
from layout_engine import Typography, resolve
from slides_native import NativeTypography, serialize_define_text_block

source_path, font_path, output_path, reveal_root = map(Path, sys.argv[1:])
source = json.loads(source_path.read_text())
proj_dir = Path(__file__).resolve().parent
style_path = source_path.with_name('styles.json')
if not style_path.exists(): style_path = proj_dir / 'styles.json'
style_data = json.loads(style_path.read_text())
assert len({s['id'] for s in source}) == len(source), f"Duplicate slide IDs found: {[s['id'] for s in source]}"
assert len(source) >= 1, "Expected at least 1 slide"
out = output_path
out.mkdir(parents=True, exist_ok=True)
(out / 'fonts').mkdir(exist_ok=True)
alt_font = source_path.with_name('DINAlternate-Bold.ttf')
if not alt_font.exists(): alt_font = proj_dir / 'DINAlternate-Bold.ttf'
if not alt_font.exists(): alt_font = proj_dir / 'fonts' / 'DINAlternate-Bold.ttf'
font_paths = {'DINCondensed-Bold': font_path, 'DINAlternate-Bold': alt_font}
typography=Typography(font_paths)
native_typography=NativeTypography()
ref_path = source_path.with_name('reference-layouts.json')
if not ref_path.exists(): ref_path = proj_dir / 'reference-layouts.json'
reference_data=json.loads(ref_path.read_text())
design_path = source_path.with_name('layout-design.json')
if not design_path.exists(): design_path = proj_dir / 'layout-design.json'
design=json.loads(design_path.read_text())
font_css=''.join(f'@font-face{{font-family:{name};src:url(data:font/ttf;base64,{base64.b64encode(path.read_bytes()).decode()})}}' for name,path in font_paths.items())
scripture_texts = []
for s in source:
    if s.get("type", "scripture") == "scripture":
        scripture_texts.append(s.get("title", "") + s.get("reference", "") + "".join(s.get("boxes", [])))
characters = set("".join(scripture_texts).upper())
for name,cmap in typography.maps.items():
    missing=sorted(characters-set(map(chr,cmap)))
    if missing: raise ValueError(f'{name} lacks characters: {missing}')

def width(text, size, font='DINCondensed-Bold'):
    return typography.width(text,size,font)

def text_runs(text, x, y, size, color, box_index=None, opacity=1, highlights=(), font='DINCondensed-Bold', line_width=None):
    result = []
    colors = {h['text'].upper():h['color'] for h in highlights}
    pattern = re.compile(r'(?<!\w)(' + '|'.join(re.escape(t) for t in sorted(colors,key=len,reverse=True)) + r')(?!\w)') if colors else None
    parts = re.split(pattern, text) if pattern else [text]
    for part in parts:
        if not part:
            continue
        factor=line_width/width(text,size,font) if line_width is not None and text else 1
        w = width(part, size, font)*factor
        result.append(dict(text=part, x=x, y=y, font=font, size=size,
                           color=colors.get(part,color), width=w,
                           opacity=opacity, boxIndex=box_index))
        x += w
    return result

deck = []
definitions = []
all_definitions = []
fit_report = []
static_sections = []

def native_block(component, cls, opacity=1):
    frame=component['frame']
    x,y,w,h=frame
    fitted=component['fitted']
    font=fitted['font'];size=fitted['fontSize'];line_height=fitted['lineHeight']
    value=[]
    for line,line_width in zip(fitted['lines'],fitted['lineWidths']):
        row=text_runs(line,0,0,size,component['color'],highlights=component.get('highlights',[]),font=font,line_width=line_width)
        value.append(''.join(f'<span style="color:{run["color"]}">{html.escape(run["text"])}</span>' for run in row))
    # Match the SVG baseline to the CSS line box, including half-leading.
    first_baseline=(line_height+fitted['ascender']-fitted['descender'])*size/2
    top=max(0,fitted['baseline']-y-first_baseline)
    left=fitted['x']-x
    right=max(0,fitted['paddingRight'])
    fill=component.get('fill')
    block = {'type':'text','x':x,'y':y,'width':w,'height':h,'value':'<br>'.join(value),
            'format':'p','align':'left','padding':0,'class':cls,'text-layout':'fixed',
            'font-family':font,'font-size-px':size,'line-height':line_height,
            'color':component['color'],
            'opacity':opacity,
            'role':'passage' if cls.startswith('box-') else 'label',
            'label-kind':'heading' if cls.startswith('heading-') else 'reference' if cls.startswith('reference-') else None,
            'heading-policy':component.get('headingPolicy'),
            'reference-policy':component.get('referencePolicy'),
            'width-policy':component.get('widthPolicy',{'mode':'reference'}),
            'native-width-policy':component.get('nativeWidthPolicy',{}),
            'width-scale':component.get('widthScale',1),
            'focus-sizing':component.get('focusSizing'),
            'background-color':fill,'text-insets':{'top':top,'right':right,'bottom':0,'left':left}}
    return serialize_define_text_block(block,native_typography.fit(block))

for slide_number, slide in enumerate(source, 1):
    slide_type = slide.get("type", "scripture")
    if slide_type == "scripture":
        assert slide['title'] and slide['reference'] and slide['boxes']
        slide_style = style_data['slides'][slide['id']]
        assert len(slide_style['boxes']) == len(slide['boxes']), 'Box style count mismatch: ' + slide['id']
        title = slide['title'].upper()
        reference = slide['reference'].upper()
        layout=resolve(slide,style_data,reference_data,design,typography)
        heading_runs = []
        heading_blocks=[]
        for kind in ['heading','reference']:
            fitted=layout[kind]
            for j,line in enumerate(fitted['lines']):
                heading_runs+=text_runs(line,fitted['x'],fitted['baseline']+j*fitted['fontSize']*fitted['lineHeight'],
                                        fitted['fontSize'],fitted['color'],font=fitted['font'],line_width=fitted['lineWidths'][j])
            heading_blocks.append(native_block({'frame':fitted['frame'],'fitted':fitted,'color':fitted['color'],'headingPolicy':fitted.get('headingPolicy'),'referencePolicy':fitted.get('referencePolicy')},f'{kind}-{slide_number}'))
        states = []
        count = len(slide['boxes'])
        for active,state_layout in enumerate(layout['builds']):
            runs = list(heading_runs)
            shapes = [dict(x=0,y=0,width=1920,height=1080,fill=layout['background'],opacity=1,boxIndex=None)]
            blocks=list(heading_blocks)
            components=[]
            box_stats = []
            for i,box in enumerate(state_layout['boxes']):
                x,y,w,height=box['frame']
                fitted=box['fitted'];size=fitted['fontSize'];lines=fitted['lines']
                bg=box['fill'];color=box['text']['color'];opacity=box['opacity']
                if x<0 or y<0 or x+w>1920.01 or y+height>1080.01: raise ValueError(f'Frame outside canvas: {slide["id"]} build {active+1} box {i+1}')
                if bg:
                    shapes.append(dict(x=x,y=y,width=w,height=height,fill=bg,opacity=opacity,boxIndex=i))
                for j, line in enumerate(lines):
                    runs+=text_runs(line,fitted['x'],fitted['baseline']+j*size*fitted['lineHeight'],size,color,i,opacity,
                                    box.get('highlights',[]),fitted['font'],fitted['lineWidths'][j])
                # The same class is intentional in every build of a logical
                # slide: Slides.com blocks are structural clones, not separate
                # focused/inactive layouts.
                cls = f'box-{slide_number}-{i}'
                blocks.append(native_block({'frame':box['frame'],'fitted':fitted,'fill':bg,'color':color,'highlights':box.get('highlights',[]),'widthPolicy':box['widthPolicy'],'nativeWidthPolicy':box['nativeWidthPolicy'],'widthScale':box['widthScale'],'focusSizing':box.get('focusSizing')},cls,opacity))
                components.append({'boxIndex':i,'frame':box['frame'],'opacity':opacity,'lines':lines,'widthPolicy':box['widthPolicy'],'readability':box.get('focusSizing')})
                box_stats.append(dict(box=i+1,font=fitted['font'],fontSize=size,desiredFontSize=fitted['desiredFontSize'],lines=len(lines),textLines=lines,
                                      width=w,height=height,x=x,y=y,bgColor=bg,opacity=opacity,widthPolicy=box['widthPolicy'],widthAdjustment=box.get('widthAdjustment'),readability=box.get('focusSizing')))
            states.append(dict(shapes=shapes,runs=runs,components=components))
            fit_report.append(dict(slide=slide_number,slideId=slide['id'],build=active+1,profile=layout['profile'],archetype=layout['archetype'],referencePage=state_layout.get('referencePage') if layout['referenceBacked'] else None,boxes=box_stats))
            definition = {'id':f'{slide["id"]}-build-{active+1}','background-color':layout['background'],'notes':slide['context'],'blocks':blocks}
            all_definitions.append(definition)
            if active==0:
                definitions.append(definition)
            svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" width="1920" height="1080" class="slide-art">']
            for shape in shapes:
                if shape['boxIndex'] is None:
                    svg.append(f'<rect x="{shape["x"]}" y="{shape["y"]}" width="{shape["width"]}" height="{shape["height"]}" fill="{shape["fill"]}"/>')
            for i in range(count):
                component=components[i]
                frame=','.join(str(v) for v in component['frame'])
                svg.append(f'<g opacity="{component["opacity"]}" data-box-index="{i}" data-frame="{frame}">')
                for shape in shapes:
                    if shape['boxIndex']==i:
                        svg.append(f'<rect x="{shape["x"]}" y="{shape["y"]}" width="{shape["width"]}" height="{shape["height"]}" fill="{shape["fill"]}"/>')
                for run in runs:
                    if run['boxIndex']==i:
                        svg.append(f'<text x="{run["x"]}" y="{run["y"]}" font-family="{run["font"]}" font-size="{run["size"]}" textLength="{run["width"]}" lengthAdjust="spacingAndGlyphs" fill="{run["color"]}" style="white-space:pre">{html.escape(run["text"])}</text>')
                svg.append('</g>')
            for run in heading_runs:
                svg.append(f'<text x="{run["x"]}" y="{run["y"]}" font-family="{run["font"]}" font-size="{run["size"]}" textLength="{run["width"]}" lengthAdjust="spacingAndGlyphs" fill="{run["color"]}">{html.escape(run["text"])}</text>')
            svg.append('</svg>')
            static_sections.append(f'<section data-slide-id="{slide["id"]}" data-build-state="{active+1}">'+''.join(svg)+f'<aside class="notes">{html.escape(slide["context"])}</aside></section>')
        deck.append(dict(id=slide['id'],title=title,reference=reference,context=slide['context'],fonts={name:f'fonts/{name}.ttf' for name in font_paths},buildStates=states))
    elif slide_type == "citation":
        assert slide.get("id"), "Citation slide missing id"
        assert slide.get("citation_id"), f"Citation slide {slide.get('id')} missing citation_id"
        assert slide.get("asset"), f"Citation slide {slide.get('id')} missing asset"
        source_info = slide.get("source", {})
        author = source_info.get("author", "")
        book_title = source_info.get("title", "")
        page_label = source_info.get("page_label", "")
        notes = slide.get("notes", "")
        bg_color = slide.get("background", "#212121")
        layout_mode = slide.get("layout", "citation-focused")

        # Format page reference according to priority display policy:
        # 1. printed_page_number -> "p. 123"
        # 2. page_label -> "PDF page iii"
        # 3. pdf_page_index -> "PDF page 25" (index + 1)
        # Never display "p. 25" when 25 is merely the physical PDF position.
        printed_page = source_info.get("printed_page_number")
        page_label = source_info.get("page_label")
        pdf_page_index = source_info.get("pdf_page_index")

        if printed_page is not None and str(printed_page).strip():
            page_ref = f"p. {str(printed_page).strip()}"
        elif page_label is not None and str(page_label).strip():
            lbl_str = str(page_label).strip()
            page_ref = lbl_str if lbl_str.lower().startswith("pdf page") else f"PDF page {lbl_str}"
        elif pdf_page_index is not None:
            try:
                page_ref = f"PDF page {int(pdf_page_index) + 1}"
            except (ValueError, TypeError):
                page_ref = ""
        else:
            page_ref = ""

        title_part = f'"{book_title}"' if book_title else ""
        label_parts = [p for p in [author, title_part, page_ref] if p]
        bib_label = " — ".join(label_parts)

        # Asset verification
        asset_rel = slide["asset"].lstrip("/")
        asset_candidates = [
            source_path.parent / asset_rel,
            reveal_root / asset_rel,
            output_path / asset_rel
        ]
        asset_found = any(c.exists() for c in asset_candidates)
        if not asset_found:
            raise ValueError(f"Citation asset not found: {slide['asset']} (checked {asset_candidates[0]})")

        img_x, img_y, img_w, img_h = 160, 60, 1600, 900
        lbl_x, lbl_y = 160, 1010

        svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" width="1920" height="1080" class="slide-art">']
        svg.append(f'<rect x="0" y="0" width="1920" height="1080" fill="{bg_color}"/>')
        svg.append(f'<image href="{html.escape(slide["asset"])}" x="{img_x}" y="{img_y}" width="{img_w}" height="{img_h}" preserveAspectRatio="xMidYMid meet"/>')
        if bib_label:
            svg.append(f'<text x="{lbl_x}" y="{lbl_y}" font-family="DINAlternate-Bold, sans-serif" font-size="28" fill="#888888" xml:space="preserve">{html.escape(bib_label)}</text>')
        svg.append('</svg>')

        static_sections.append(
            f'<section data-slide-id="{slide["id"]}" data-build-state="1" data-slide-type="citation">'
            f'{"".join(svg)}'
            f'<aside class="notes">{html.escape(notes)}</aside></section>'
        )

        state = {
            "shapes": [{"x": 0, "y": 0, "width": 1920, "height": 1080, "fill": bg_color, "opacity": 1, "boxIndex": None}],
            "images": [{"href": slide["asset"], "x": img_x, "y": img_y, "width": img_w, "height": img_h, "preserveAspectRatio": "xMidYMid meet"}],
            "runs": [{"text": bib_label, "x": lbl_x, "y": lbl_y, "font": "DINAlternate-Bold", "size": 28, "color": "#888888", "width": 1600, "opacity": 1, "boxIndex": None}] if bib_label else [],
            "components": []
        }
        deck.append(dict(
            id=slide["id"],
            type="citation",
            citation_id=slide["citation_id"],
            asset=slide["asset"],
            full_page_asset=slide.get("full_page_asset"),
            source=source_info,
            layout=layout_mode,
            title=bib_label or slide["id"],
            reference="",
            context=notes,
            notes=notes,
            fonts={name: f"fonts/{name}.ttf" for name in font_paths},
            buildStates=[state]
        ))

        img_block = {
            "type": "image",
            "x": img_x,
            "y": img_y,
            "width": img_w,
            "height": img_h,
            "src": slide["asset"],
            "class": f"citation-img-{slide_number}"
        }
        blocks = [img_block]
        if bib_label:
            raw_lbl = {
                "type": "text", "x": 160, "y": 980, "width": 1600, "height": 50,
                "value": f'<span style="color:#888888">{html.escape(bib_label)}</span>',
                "format": "p", "align": "left", "padding": 0,
                "class": f"citation-label-{slide_number}", "text-layout": "fixed",
                "font-family": "DINAlternate-Bold", "font-size-px": 28, "line-height": 1.25,
                "color": "#888888", "role": "label", "label-kind": "citation",
                "background-color": None, "text-insets": {"top": 0, "right": 0, "bottom": 0, "left": 0}
            }
            blocks.append(serialize_define_text_block(raw_lbl, native_typography.fit(raw_lbl)))

        definition = {
            "id": f"{slide['id']}-build-1",
            "background-color": bg_color,
            "notes": notes,
            "blocks": blocks
        }
        definitions.append(definition)
        all_definitions.append(definition)
    else:
        raise ValueError(f"Unknown slide type: {slide_type} on slide {slide.get('id')}")

for name, data in [('slides.json',deck),('source.json',source),('styles.json',style_data),('fit-report.json',fit_report)]:
    (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
for filename, slides in [('slides-com-editable-18.json',definitions),('slides-com-editable.json',definitions),('slides-com-builds.json',all_definitions)]:
    data = dict(title='True Humanity: From the Garden to Nineveh',width=1920,height=1080,transition='none',css='',slides=slides)
    data.update({'theme-font':'league','theme-color':'black-blue'})
    (out/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
for name,path in font_paths.items(): shutil.copyfile(path,out/'fonts'/f'{name}.ttf')
css = (reveal_root/'css'/'theme'/'keynote.css').read_text()
static = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>True Humanity</title><link rel="stylesheet" href="dist/reset.css"><link rel="stylesheet" href="dist/reveal.css"><style>'+font_css+css+'</style></head><body><div class="reveal"><div class="slides">'+''.join(static_sections)+'</div></div><script src="dist/reveal.js"></script><script>(async()=>{const print=new URLSearchParams(location.search).has("print-pdf");if(print)document.documentElement.classList.add("print-deck","print-pdf");await Promise.all([document.fonts.load("72px DINCondensed-Bold"),document.fonts.load("72px DINAlternate-Bold")]);await document.fonts.ready;if(!print)await Reveal.initialize({width:1920,height:1080,margin:0,center:false,hash:true,transition:"none",controls:false,progress:false});window.presentationReady=true;})();</script></body></html>'
(out/'index.html').write_text(static)
(out/'reveal-import.html').write_text(static)
(out/'dist').mkdir(exist_ok=True)
for name in ['reset.css','reveal.css','reveal.js']:
    shutil.copyfile(reveal_root/'dist'/name,out/'dist'/name)
min_font = min(b['fontSize'] for f in fit_report for b in f['boxes']) if any(f.get('boxes') for f in fit_report) else 0
print(json.dumps({'logicalSlides':len(deck),'buildPages':len(all_definitions),'minFont':min_font}))
