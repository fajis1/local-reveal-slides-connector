"""Allocate passage space jointly, with one canonical wrap per component.

The reference supplies palette, order, anchors and preferred vertical centers.
Current content and both actual target fonts supply dimensions. Every build is
part of the same constraint calculation; old neighbor rectangles are not limits.
"""
from functools import lru_cache
from itertools import product
from math import floor
import re

from slides_native import NativeTypography
from width_policy import preserve_horizontal_anchor


@lru_cache(None)
def native_metrics():
    return NativeTypography()


def target_metrics(typography, font):
    native = native_metrics()
    face = native.fonts['alternate' if font == 'DINAlternate-Bold' else 'condensed']
    cmap = face.getBestCmap()
    em = face['head'].unitsPerEm
    width = lambda text: sum(face['hmtx'].metrics[cmap[ord(c)]][0] for c in text) / em
    return width, (face['hhea'].ascent-face['hhea'].descent)/em


def balanced_wrap(text, count, advance, limit):
    """Fit a fixed number of lines, preferring punctuation and balanced phrases."""
    words = text.split()
    @lru_cache(None)
    def solve(start, remaining):
        if not remaining:
            return (0, 0, ()) if start == len(words) else None
        best = None
        for end in range(start+1, len(words)-remaining+2):
            line = ' '.join(words[start:end])
            width = advance(line)
            if width > limit+1e-7:
                break
            tail = solve(end, remaining-1)
            if tail is None:
                continue
            punctuation = bool(re.search(r'[,.;!?]["”’\')]*$', words[end-1]))
            # First minimize the widest line; punctuation breaks break ties.
            score = (max(width,tail[0]), tail[1]+(width/limit)**2-(.05 if punctuation and remaining>1 else 0))
            if best is None or score < best[:2]:
                best = (*score, (line,)+tail[2])
        return best
    selected = solve(0, count)
    return list(selected[2]) if selected else None


def project_centers(preferred, heights, top, bottom, gap):
    """Least-squares projection onto ordered, separated component positions."""
    offsets = [heights[0]/2]
    for i in range(1, len(heights)):
        offsets.append(offsets[-1]+(heights[i-1]+heights[i])/2+gap)
    # Isotonic regression preserves useful reference staggering in y without
    # adding identical rows or letting an expanded component overlap a neighbor.
    pools = []
    for i, value in enumerate(preferred):
        pools.append([value-offsets[i], 1, [i]])
        while len(pools)>1 and pools[-2][0]/pools[-2][1] > pools[-1][0]/pools[-1][1]:
            a,b = pools[-2:]
            pools[-2:] = [[a[0]+b[0], a[1]+b[1], a[2]+b[2]]]
    upper = bottom-offsets[-1]-heights[-1]/2
    positions = [0]*len(heights)
    for total, weight, indices in pools:
        value = max(top, min(upper, total/weight))
        for i in indices:
            positions[i] = value+offsets[i]
    return positions


def allocate_readable_slide(states, slide, styles, policy, typography, reference_backed=False):
    count = len(slide['boxes'])
    top, bottom = policy['top'], policy['bottom']
    gap = policy['gap']
    px, py = policy['paddingHorizontal'], policy['paddingVertical']
    margin = policy['margin']
    options = []
    components = []
    for i, text in enumerate(slide['boxes']):
        box = states[i]['boxes'][i]
        original = list(box['frame'])
        font = box['text']['font']
        desired = policy.get('fontSizes', {}).get(font, policy['preferredFontSize'])
        anchor = styles['boxes'][i].get('anchor', 'left')
        x,y,w,h = original
        if anchor == 'left':
            x = max(margin, min(x, policy['maximumLeftInset']))
            w = 1920-margin-x
        elif anchor == 'right':
            right = min(1920-margin, x+w)
            x = margin; w = right-x
        else:
            center = x+w/2
            w = 2*min(center-margin, 1920-margin-center)
            x = center-w/2
        native_width, native_leading = target_metrics(typography, font)
        # Use the stricter font at each line, preserving the very same wrap for
        # DIN/League Gothic or DIN Alternate/Arial without font substitution drift.
        advance = lambda s, font=font, nw=native_width: max(typography.width(s,1,font), nw(s))
        available = w-2*px-policy['allowance']
        text = text.upper()
        candidates = []
        # Try full comfortable size first; fewer lines at the lower comfortable
        # size can save vertical space and permit bigger text elsewhere.
        for lines in range(1, policy['maximumLines']+1):
            selected = balanced_wrap(text, lines, advance, available/(desired*policy['wrapTradeoffFloor']))
            if selected:
                cap = min(desired, available/max(advance(line) for line in selected))
                asc,desc = typography.vertical(font)
                leading = max(policy['lineHeight'], asc+desc)
                coefficient = max(asc+desc+(lines-1)*leading, lines*max(leading,native_leading))
                candidates.append(dict(lines=selected, cap=cap, desired=desired,
                                       coefficient=coefficient, leading=leading))
                # Additional lines cannot improve capped font size and consume
                # more space in every build, so they are dominated alternatives.
                if cap>=desired-.001:
                    break
        if not candidates:
            raise ValueError('No readable wrap within configured line limit: '+slide['id']+' '+str(i+1))
        options.append(candidates)
        components.append(dict(original=original,font=font,anchor=anchor,envelope=[x,y,w,h],
                               center=y+h/2,nativeLeading=native_leading))

    def heights_for(candidate, sizes):
        return [c['coefficient']*sizes[i]+2*py+policy['allowance'] for i,c in enumerate(candidate)]

    best = None
    for candidate in product(*options):
        available = bottom-top-(count-1)*gap
        fixed_padding = (2*py+policy['allowance'])*count
        sizes = [c['cap'] for c in candidate]
        # Share a genuine height shortage according to each passage's height
        # contribution, instead of shrinking short width-constrained strips by
        # the same global factor as much taller quotations.
        for _ in range(count*3):
            coefficients = [c['coefficient'] for c in candidate]
            violation = sum(a*s for a,s in zip(coefficients,sizes))-(available-fixed_padding)
            if violation <= .001:
                break
            denominator = sum((a*c['desired'])**2 for a,c in zip(coefficients,candidate))
            sizes = [s-violation*a*c['desired']**2/denominator for s,a,c in zip(sizes,coefficients,candidate)]
        sizes = [floor(s*4)/4 for s in sizes]
        # Favor comfortable reading across the whole slide, with a penalty for
        # an unusually small passage. Extra lines compete on their actual cost.
        score = (sum(s/c['desired'] for s,c in zip(sizes,candidate))-.2*sum((1-s/c['desired'])**2 for s,c in zip(sizes,candidate)),
                 min(s/c['desired'] for s,c in zip(sizes,candidate)), sum(sizes),
                 -sum(len(c['lines']) for c in candidate))
        if best is None or score>best[0]:
            best = score, candidate, sizes
    _, selected, sizes = best
    if min(sizes)<policy['minimumFontSize']:
        raise ValueError('Slide needs a different archetype to remain readable: '+slide['id']+
                         ' (canonical font sizes: '+', '.join(f'{size:g}' for size in sizes)+')')

    # Choose meaningful punctuation breaks while the full horizontal envelope
    # is still available. Trimming a colored frame first can wrongly reject a
    # good comma break (for example, "RAHAB IN PIECES,").
    for i, (definition, component) in enumerate(zip(selected,components)):
        fitted={'lines':definition['lines'],'fontSize':sizes[i],'font':component['font'],
                'paddingLeft':px,'paddingRight':px}
        lines=typography.comma_wrap(slide['boxes'][i].upper(),fitted,component['envelope'])
        native_width,_=target_metrics(typography,component['font'])
        available=component['envelope'][2]-2*px-policy['allowance']
        if max(max(typography.width(line,sizes[i],component['font']),native_width(line)*sizes[i]) for line in lines)<=available:
            definition['lines']=lines

    heights = heights_for(selected, sizes)
    centers = project_centers([c['center'] for c in components], heights, top, bottom, gap)
    canonical = []
    for i, (definition, component) in enumerate(zip(selected, components)):
        size = sizes[i]
        native_width,_ = target_metrics(typography,component['font'])
        needed = max(max(typography.width(line,size,component['font']),native_width(line)*size) for line in definition['lines'])+2*px+policy['allowance']
        width = min(component['envelope'][2], max(needed,component['original'][2])) if reference_backed and states[i]['boxes'][i]['fill'] else needed
        x,y,w,h = preserve_horizontal_anchor(component['envelope'],width,component['anchor'])
        asc,desc = typography.vertical(component['font'])
        reveal_height = (asc+desc+(len(definition['lines'])-1)*definition['leading'])*size+2*py
        y = centers[i]-reveal_height/2
        box = states[i]['boxes'][i]
        box['frame'] = [x,y,w,reveal_height]
        box['text'].update(fontSize=size,x=x+px,
                           bbox=[x+px,y+py,x+w-px,y+reveal_height-py],
                           lines=definition['lines'],
                           lineWidths=[typography.width(line,size,component['font']) for line in definition['lines']],
                           lineHeight=definition['leading'])
        box['focusSizing'] = {'preferredFontSize':definition['desired'],
                              'paddingVertical':py,
                              'availableRow':[centers[i]-heights[i]/2,centers[i]+heights[i]/2],
                              'anchor':component['anchor'],'originalFrame':component['original'],
                              'wholeSlide':True,'lineCount':len(definition['lines']),
                              'reason':'preferred-size' if abs(size-definition['desired'])<.26 else
                                       'whole-slide-height' if size<definition['cap']-.26 else 'line-width',
                              'allocatedFontSize':size}
        canonical.append(box)
    for active, state in enumerate(states):
        state['boxes'] = [dict(box) for box in canonical]
        for index, box in enumerate(state['boxes']):
            # Deep-copy nested geometry/text dictionaries to prevent later fits
            # from mutating another progression state.
            box['frame'] = list(box['frame'])
            box['text'] = dict(box['text'])
            box['text']['bbox'] = list(box['text']['bbox'])
            box['text']['lines'] = list(box['text']['lines'])
            box['text']['lineWidths'] = list(box['text']['lineWidths'])
            box['opacity'] = 1 if index == active else .5
