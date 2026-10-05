"""Target-independent width policies, applied after target-font text fitting."""
def preserve_horizontal_anchor(frame, width, anchor):
    x, y, old_width, height = frame
    if anchor == 'right':
        x += old_width - width
    elif anchor == 'center':
        x += (old_width - width) / 2
    elif anchor != 'left':
        raise ValueError('Unknown horizontal anchor: ' + anchor)
    return [x, y, width, height]


def apply_content_width_policy(frame, line_widths, policy, scale=1):
    """Retain reference frames or wrap fitted content with scaled design padding."""
    mode = policy.get('mode', 'reference')
    if mode == 'reference':
        return list(frame), None
    if mode not in ('content', 'content-clamped'):
        raise ValueError('Unknown width policy: ' + mode)
    left = policy.get('paddingLeft', 12) * scale
    right = policy.get('paddingRight', 12) * scale
    desired = max(line_widths, default=0) + left + right
    minimum = policy.get('minWidth', 0) * scale if mode == 'content-clamped' else 0
    maximum = min(frame[2], policy.get('maxWidth', frame[2])) if mode == 'content-clamped' else 1920
    # Long fitted quotations may already fill their original envelope. Retain
    # the envelope and reduce optional padding instead of clipping or rewrapping.
    if maximum + .01 < desired:
        available_padding=maximum-max(line_widths,default=0)
        if available_padding < 0:
            raise ValueError('Content exceeds the fitted design envelope')
        ratio=available_padding/(left+right) if left+right else 0
        left*=ratio; right*=ratio; desired=maximum
    width = min(maximum, max(minimum, desired))
    result = preserve_horizontal_anchor(frame, width, policy.get('anchor', 'left'))
    if result[0] < 0 or result[0] + width > 1920.01:
        raise ValueError('Content width leaves the presentation canvas')
    return result, {'mode': mode, 'anchor': policy.get('anchor', 'left'),
                    'paddingLeft': left, 'paddingRight': right,
                    'originalFrame': list(frame), 'textWidth': max(line_widths, default=0)}
