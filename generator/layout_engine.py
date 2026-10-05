"""Reference frames plus content fitting; no equal-height slide-wide formula."""
from copy import deepcopy
from functools import lru_cache
import re
from fontTools.ttLib import TTFont
from width_policy import apply_content_width_policy, preserve_horizontal_anchor
from readable_layout import allocate_readable_slide

def normalized(text): return re.sub(r'\s+',' ',text).strip()

class Typography:
    def __init__(self, paths):
        self.fonts={name:TTFont(path) for name,path in paths.items()}
        self.maps={name:font.getBestCmap() for name,font in self.fonts.items()}

    def width(self,text,size,font):
        face=self.fonts[font]
        return sum(face['hmtx'].metrics[self.maps[font][ord(c)]][0] for c in text)*size/face['head'].unitsPerEm

    def vertical(self,font):
        face=self.fonts[font]
        return face['hhea'].ascent/face['head'].unitsPerEm, -face['hhea'].descent/face['head'].unitsPerEm

    def wrap(self,text,size,limit,font):
        result=[]
        line=''
        for word in text.split(' '):
            candidate=(line+' '+word) if line else word
            if line and self.width(candidate,size,font)>limit:
                result.append(line)
                line=word
            else: line=candidate
        if line: result.append(line)
        return result

    def comma_wrap(self,text,fitted,frame):
        """Prefer comma boundaries without adding lines or overflowing the frame."""
        existing=fitted['lines']
        count=len(existing)
        if count<2 or ',' not in text: return existing
        words=text.split(' ')
        space=self.width(' ',fitted['fontSize'],fitted['font'])
        prefix=[0]
        for word in words:
            prefix.append(prefix[-1]+self.width(word,fitted['fontSize'],fitted['font'])+space)
        limit=frame[2]-fitted['paddingLeft']-fitted['paddingRight']
        target=(prefix[-1]-space*count)/count
        comma=lambda word: bool(re.search(r',["”’\')]*$',word))

        @lru_cache(None)
        def partition(start,remaining):
            if remaining==0: return (0,0,()) if start==len(words) else None
            best=None
            for end in range(start+1,len(words)-remaining+2):
                width=prefix[end]-prefix[start]-space
                if width>limit+.01: break
                tail=partition(end,remaining-1)
                if tail is None: continue
                score=tail[0]+(int(comma(words[end-1])) if remaining>1 else 0)
                balance=tail[1]-((width-target)/limit)**2
                candidate=(score,balance,(' '.join(words[start:end]),)+tail[2])
                if best is None or candidate[:2]>best[:2]: best=candidate
            return best

        selected=partition(0,count)
        current=sum(comma(line) for line in existing[:-1])
        return list(selected[2]) if selected and selected[0]>current else existing

    def fit(self,text,frame,hint,preferred=None,minimum=28):
        x,y,w,h=frame
        asc,desc=self.vertical(hint['font'])
        bbox=hint['bbox']
        # Reference insets are retained, including deliberate large top insets.
        # Browser glyph bearings / integer font bounds need a small safe inset.
        left=max(2.1,hint['x']-x)
        top=max(2.1,bbox[1]-y)
        right=max(2.1,x+w-bbox[2])
        bottom=max(2.1,y+h-bbox[3])
        if left+right>=w*.5: left=right=4
        if top+bottom>=h*.6: top=bottom=4
        available_w=w-left-right
        available_h=h-top-bottom
        desired=hint['fontSize']
        exact_lines=preferred or (hint['lines'] if normalized(' '.join(hint['lines']))==normalized(text) else None)
        line_height=hint.get('fixedLineHeight',hint['lineHeight'])
        # A single-line strip does not impose its old paragraph's line spacing
        # on new quotations. Multi-line hints keep measured baseline spacing.
        if not exact_lines: line_height=max(line_height,asc+desc)
        ticks=round(desired*4)
        for tick in range(ticks,round(minimum*4)-1,-1):
            size=min(desired,tick/4)
            lines=exact_lines or self.wrap(text,size,available_w,hint['font'])
            widths=[self.width(line,size,hint['font']) for line in lines]
            if lines==hint['lines']:
                # The PDF carries optical kerning absent from the subset font.
                # Its measured line advances are authoritative for unchanged lines.
                widths=[width*size/desired for width in hint['lineWidths']]
            if lines==hint.get('fixedLines'):
                widths=[advance*size for advance in hint['fixedAdvances']]
            height=(asc+desc+(len(lines)-1)*line_height)*size
            if max(widths,default=0)<=available_w+.01 and height<=available_h+.02:
                return {'font':hint['font'],'fontSize':size,'lines':lines,'lineWidths':widths,
                        'x':x+left,'baseline':y+top+asc*size,'lineHeight':line_height,
                        'ascender':asc,'descender':desc,'paddingLeft':left,'paddingRight':right,
                        'paddingTop':top,'paddingBottom':bottom,'desiredFontSize':desired}
        raise ValueError(f'Text cannot fit frame {frame} at minimum {minimum}: {text}')

def remap(box,changes):
    """Move/resize one complete passage component and preserve its text insets."""
    result=deepcopy(box)
    old_x,old_y,old_w,old_h=result['frame']
    x=changes.get('x',old_x); y=changes.get('y',old_y)
    w=changes.get('width',old_w); h=changes.get('height',old_h)
    text=result['text']
    left=max(4,text['x']-old_x)
    top=max(4,text['bbox'][1]-old_y)
    right=max(4,old_x+old_w-text['bbox'][2])
    bottom=max(4,old_y+old_h-text['bbox'][3])
    if top+bottom>h*.4: top=bottom=5
    result['frame']=[x,y,w,h]
    text['x']=x+left
    text['bbox']=[x+left,y+top,x+w-right,y+h-bottom]
    text['fontSize']=changes.get('fontSize',text['fontSize'])
    text['baseline']=y+top+text['ascender']*text['fontSize']
    if changes.get('font'): text['font']=changes['font']
    return result


def canonicalize_progression_states(states, count):
    """Make every build state a clone of one logical-slide layout.

    Each component takes its focused geometry and styling as the canonical
    design. Build states then differ solely by the component opacity. This is
    deliberately done before fitting/native serialization, so those stages see
    one immutable layout rather than repairing per-state output afterward.
    """
    canonical = []
    for index in range(count):
        box = deepcopy(states[index]['boxes'][index])
        colors = {item['text'].upper(): item['color'] for item in box.get('highlights', [])}
        words = sorted({item['text'].upper() for state in states for item in state['boxes'][index].get('highlights', [])})
        box['highlights'] = [{'text': word, 'color': colors.get(word, box['text']['color'])} for word in words]
        box['opacity'] = 1
        canonical.append(box)
    for active, state in enumerate(states):
        state['boxes'] = [deepcopy(box) for box in canonical]
        for index, box in enumerate(state['boxes']):
            box['opacity'] = 1 if index == active else .5

def size_focus_component(states,index,text,anchor,policy,typography,header_bottom):
    """Fit an opted-in passage once and clone it across the progression.

    Neighbor envelopes across every build bound the free row. The measured text
    determines its actual height; focus changes only the cloned component's
    opacity, never its geometry, typography, padding, or color.
    """
    active=states[index]['boxes'][index]
    original=list(active['frame'])
    margin=policy['margin'];gap=policy['gap']
    top=max([header_bottom+gap]+[b['frame'][1]+b['frame'][3]+gap
                               for state in states for b in state['boxes'][:index]])
    bottom=min([1080-gap]+[b['frame'][1]-gap
                           for state in states for b in state['boxes'][index+1:]])
    if anchor=='left':
        x=margin;width=1920-2*margin
    elif anchor=='right':
        right=min(1920-margin,original[0]+original[2])
        x=margin;width=right-margin
    elif anchor=='center':
        center=original[0]+original[2]/2
        width=2*min(center-margin,1920-margin-center)
        x=center-width/2
    else:
        raise ValueError('Unknown focus anchor: '+anchor)
    px=policy['paddingHorizontal'];py=policy['paddingVertical']
    frame=[x,top,width,bottom-top]
    hint={**active['text'],'font':policy['font'],'fontSize':policy['preferredFontSize'],
          'x':x+px,'bbox':[x+px,top+py,x+width-px,bottom-py],
          'lines':[],'lineWidths':[],'lineHeight':policy['lineHeight']}
    # Let longer current content choose a better canonical wrap before shrinking
    # it into an old single-line strip. This wrap remains fixed in all builds.
    fitted=typography.fit(text.upper(),frame,hint)
    sentences=re.split(r'(?<=[.!?])\s+',text.upper())
    if len(sentences)==len(fitted['lines']) and len(sentences)>1:
        try:
            semantic_fit=typography.fit(text.upper(),frame,hint,sentences)
        except ValueError:
            semantic_fit=None
        if semantic_fit and semantic_fit['fontSize']>=fitted['fontSize']*.85:
            fitted=semantic_fit
    height=(fitted['ascender']+fitted['descender']+
            (len(fitted['lines'])-1)*fitted['lineHeight'])*fitted['fontSize']+2*py
    center=original[1]+original[3]/2
    y=max(top,min(center-height/2,bottom-height))
    active_frame=[x,y,width,height]
    canonical=deepcopy(states[index]['boxes'][index])
    canonical['frame']=active_frame
    canonical['text'].update(font=policy['font'],fontSize=fitted['fontSize'],x=x+px,
                             bbox=[x+px,y+py,x+width-px,y+height-py],
                             lines=fitted['lines'],lineWidths=fitted['lineWidths'],lineHeight=fitted['lineHeight'])
    canonical['focusSizing']={'preferredFontSize':policy['preferredFontSize'],
                              'availableRow':[top,bottom],
                              'anchor':anchor,'originalFrame':original}
    for state in states:
        box=deepcopy(canonical)
        box['opacity']=1 if state['activeBox']==index else .5
        state['boxes'][index]=box

def resolve(slide,styles,references,design,typography):
    spec=design['slides'][slide['id']]
    is_reference='referenceProfile' in spec
    profile_name=spec.get('referenceProfile') or design['archetypes'][spec['archetype']]
    profile=deepcopy(references['profiles'][profile_name])
    count=len(slide['boxes'])
    if spec.get('adaptation')=='five-passage-inversion':
        new_builds=[]
        for active,original_state in enumerate([0,1,2,2,3]):
            state=deepcopy(profile['builds'][original_state])
            state['activeBox']=active
            state['boxes'][1]=remap(state['boxes'][1],{'y':388})
            state['boxes'][2]=remap(state['boxes'][2],{'y':574})
            if active==3:
                # The added fourth passage must not leave the serpent passage
                # at the copied third build's focused size.
                state['boxes'][2]=remap(profile['builds'][0]['boxes'][2],{'y':574})
            extra=remap(state['boxes'][1],{'x':270 if active!=3 else 100,'y':706,
                                            'width':1560 if active!=3 else 1770,'height':143,
                                            'fontSize':64 if active==3 else 55})
            extra['fill']=None
            extra['text']['lines']=[]
            extra['text']['lineWidths']=[]
            state['boxes'].insert(3,extra)
            new_builds.append(state)
        profile['builds']=new_builds
    if spec.get('adaptation')=='six-passage-service':
        profile['builds'].append(deepcopy(profile['builds'][-1]))
        for active,state in enumerate(profile['builds']):
            state['activeBox']=active
            state['boxes'].append(deepcopy(state['boxes'][-1]))
    states=[]
    for active in range(count):
        state=deepcopy(profile['builds'][active])
        state['activeBox']=active
        for i,box in enumerate(state['boxes']):
            changes=spec.get('boxOverrides',[])
            if changes and i==active and 'active' in changes[i]:
                box=remap(box,changes[i]['active'])
                state['boxes'][i]=box
            box['opacity']=1 if i==active else .5
            if not is_reference:
                style={**styles['palette'][styles['slides'][slide['id']]['boxes'][i]['style']],
                       **styles['slides'][slide['id']]['boxes'][i]}
                box['fill']=style['bgColor']
                box['text']['color']=style['textColor']
                # New slide content uses the condensed font unless it explicitly
                # selects the five-strip DIN Alternate warning archetype.
                if profile_name!='liar-murderer': box['text']['font']='DINCondensed-Bold'
                if spec.get('adaptation')=='six-passage-service': box['text']['font']='DINCondensed-Bold'
                # Clear old quotation hints from new archetype content.
                box['text']['lines']=[]
                box['text']['lineWidths']=[]
            if spec.get('adaptation')=='current-closing-passages' and i>=3:
                box=remap(box,{'x':90 if i==3 else 260,'y':668 if i==3 else 907,
                              'width':1780 if i==3 else 1580,'height':143,
                              'fontSize':88 if active==i else 69})
                state['boxes'][i]=box
                box['text']['lines']=[];box['text']['lineWidths']=[]
            custom=styles['slides'][slide['id']]['boxes'][i].get('highlights',[])
            x,y,w,h=box['frame']
            if x<0 or y<0 or x+w>1920 or y+h>1080:
                box=remap(box,{'x':max(0,x),'y':max(0,y),
                              'width':min(w,1920-max(0,x)),
                              'height':min(h,1080-max(0,y))})
                state['boxes'][i]=box
            box['highlights']=box['text'].get('highlights',[])+custom
        states.append(state)
    # Establish one canonical component tree before any layout fitting. The
    # prior implementation used inactive overrides and alternate text colors;
    # focus is now opacity only.
    canonicalize_progression_states(states, count)
    readability = design.get('readabilityPolicy', {})
    if readability.get('enabled'):
        # Retain intentional font changes from the previous focus release.
        for key,override in spec.get('focusSizing',{}).items():
            for state in states:
                state['boxes'][int(key)-1]['text']['font']=override.get('font',design['focusSizingDefaults']['font'])
        allocate_readable_slide(states,slide,styles['slides'][slide['id']],
                                {**readability,**spec.get('readability',{})},typography,is_reference)
    # Optional stable wrap retains word relationships through progressive builds.
    preferred=[]
    advances=[]
    line_heights=[]
    for i,text in enumerate(slide['boxes']):
        focus_policy=spec.get('focusSizing',{}).get(str(i+1))
        if focus_policy is not None and not readability.get('enabled'):
            size_focus_component(states,i,text,styles['slides'][slide['id']]['boxes'][i].get('anchor','left'),
                                 {**design['focusSizingDefaults'],**focus_policy},typography,
                                 max(design.get('headingPolicy',{}).get('bottom',114),
                                     design.get('referencePolicy',{}).get('bottom',114)))
        active=states[i]['boxes'][i]
        fitted=typography.fit(text.upper(),active['frame'],active['text'])
        if design.get('preferCommaBreaks',True):
            lines=typography.comma_wrap(text.upper(),fitted,active['frame'])
            if lines!=fitted['lines']:
                hint={**active['text'],'fixedLineHeight':fitted['lineHeight']}
                fitted=typography.fit(text.upper(),active['frame'],hint,lines)
        preferred.append(fitted['lines'])
        advances.append([w/fitted['fontSize'] for w in fitted['lineWidths']])
        line_heights.append(fitted['lineHeight'])
    for state in states:
        for i,box in enumerate(state['boxes']):
            if design.get('stableLineBreaks',True):
                box['text']['fixedLines']=preferred[i]
                box['text']['fixedAdvances']=advances[i]
                box['text']['fixedLineHeight']=line_heights[i]
            box['fitted']=typography.fit(slide['boxes'][i].upper(),box['frame'],box['text'],
                                         preferred[i] if design.get('stableLineBreaks',True) else None)
    # Exact reference sequences retain their deliberately long strips. New
    # archetype content can use a fitted width without rewrapping or rescaling.
    for i in range(count):
        style=styles['slides'][slide['id']]['boxes'][i]
        policy=deepcopy(style.get('widthPolicy', spec.get('widthPolicy',
                        {'mode':'reference'} if is_reference else design.get('adaptedWidthPolicy',{'mode':'reference'}))))
        policy.setdefault('anchor',style.get('anchor','left'))
        for state in states:
            box=state['boxes'][i]
            fitted=box['fitted']
            box['widthPolicy']=policy if box['fill'] else {'mode':'reference'}
            # Native fitting uses target-font content width by default; retain
            # a historical width there only when this override explicitly says so.
            box['nativeWidthPolicy']=deepcopy(style.get('nativeWidthPolicy',spec.get('nativeWidthPolicy',{})))
            box['widthScale']=1
            frame,adjustment=apply_content_width_policy(box['frame'],fitted['lineWidths'],box['widthPolicy'],1)
            if adjustment:
                box['widthAdjustment']=adjustment
                box['frame']=frame
                fitted['x']=frame[0]+adjustment['paddingLeft']
                fitted['paddingLeft']=adjustment['paddingLeft']
                fitted['paddingRight']=adjustment['paddingRight']
    heading=deepcopy(profile['heading'])
    reference=deepcopy(profile['reference'])
    if not is_reference:
        ref_size=96 if len(slide['reference'])<17 else 72
        ref_width=min(800,typography.width(slide['reference'].upper(),ref_size,'DINCondensed-Bold')+8)
        reference.update(x=1888-ref_width,fontSize=ref_size,font='DINCondensed-Bold',lineHeight=1)
        reference['bbox']=[reference['x'],40,1888,40+ref_size]
        reference['lines']=[];reference['lineWidths']=[]
        reference['baseline']=40+ref_size*.712
        heading['lines']=[];heading['lineWidths']=[]
    reference_policy=design.get('referencePolicy')
    if reference_policy:
        # Scripture references are static labels with one preferred size and
        # a shared lower-right anchor, regardless of reference string length.
        padding=reference_policy.get('padding',4)
        ref_width=typography.width(slide['reference'].upper(),reference_policy['fontSize'],reference_policy['font'])+2*padding
        rx=reference_policy['right']-ref_width
        ry=reference_policy['bottom']-reference_policy['height']
        reference_frame=[rx,ry,ref_width,reference_policy['height']]
        reference.update(x=rx+padding,fontSize=reference_policy['fontSize'],font=reference_policy['font'],
                         lineHeight=reference_policy['lineHeight'],color=reference_policy['color'],
                         bbox=[rx+padding,ry+padding,reference_policy['right']-padding,reference_policy['bottom']-padding],
                         lines=[slide['reference'].upper()],lineWidths=[ref_width-2*padding])
    heading_policy=design.get('headingPolicy')
    if heading_policy:
        # Titles are quiet labels: one shared size and lower-left anchor across
        # the deck, with fitting only for the occasional exceptionally long title.
        heading.update(fontSize=heading_policy['fontSize'],font=heading_policy['font'],
                       x=heading_policy['x'],lineHeight=heading_policy['lineHeight'])
        heading_frame=[heading_policy['x'],heading_policy['bottom']-heading_policy['height'],
                       (reference_frame[0] if reference_policy else reference['x'])-heading_policy['x']-heading_policy['referenceGap'],heading_policy['height']]
    else:
        heading_frame=[heading['x'],heading['bbox'][1],reference['x']-heading['x']-18,heading['fontSize']*1.12]
    heading['bbox']=[heading_frame[0],heading_frame[1],heading_frame[0]+heading_frame[2],heading_frame[1]+heading_frame[3]]
    heading['lines']=[slide['title'].upper()]
    heading['lineWidths']=[typography.width(slide['title'].upper(),heading['fontSize'],heading['font'])]
    # One-line headings/references are fixed through every build of a slide.
    title_fit=typography.fit(slide['title'].upper(),heading_frame,heading,[slide['title'].upper()],minimum=heading_policy.get('minimumFontSize',36) if heading_policy else 36)
    if heading_policy:
        title_fit['baseline']=heading_policy['bottom']-title_fit['paddingBottom']-title_fit['descender']*title_fit['fontSize']
        title_fit['paddingTop']=title_fit['baseline']-heading_frame[1]-title_fit['ascender']*title_fit['fontSize']
    if not reference_policy:
        rx=reference['x'];ry=reference['bbox'][1]
        reference_frame=[rx,ry,1920-rx-12,max(reference['bbox'][3]-ry,reference['fontSize']*1.05)]
        reference['bbox']=[rx,ry,rx+reference_frame[2],ry+reference_frame[3]]
    ref_fit=typography.fit(slide['reference'].upper(),reference_frame,reference,[slide['reference'].upper()],minimum=reference_policy.get('minimumFontSize',36) if reference_policy else 36)
    if reference_policy:
        ref_fit['x']=reference_policy['right']-ref_fit['paddingRight']-max(ref_fit['lineWidths'])
        ref_fit['paddingLeft']=ref_fit['x']-reference_frame[0]
        ref_fit['baseline']=reference_policy['bottom']-ref_fit['paddingBottom']-ref_fit['descender']*ref_fit['fontSize']
        ref_fit['paddingTop']=ref_fit['baseline']-reference_frame[1]-ref_fit['ascender']*ref_fit['fontSize']
    return {'profile':profile_name,'archetype':spec.get('archetype'),'referenceBacked':is_reference,
            'background':profile['background'],'heading':{**title_fit,'color':heading['color'],'frame':heading_frame,'headingPolicy':heading_policy},
            'reference':{**ref_fit,'color':reference['color'],'frame':reference_frame,'referencePolicy':reference_policy},'builds':states}
