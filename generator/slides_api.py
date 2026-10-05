"""Slides API client. Credentials stay inside this process and never enter logs."""
import argparse
import html
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API_ROOT = 'https://api.slides.com'
MCP_ROOT = 'https://mcp.slides.com/'
# The key location is intentionally configurable.  No credential is stored in
# this project or included in generated payloads.
KEY_PATH = Path(os.environ.get('SLIDES_API_KEY_FILE', '/etc/slides-api/key'))


class ApiError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ApiError('Unexpected redirect; request stopped.')


class SecureDownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ApiError('Export redirected to an insecure destination.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SlidesClient:
    def __init__(self):
        try:
            fd = os.open(KEY_PATH, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'r', encoding='utf-8') as credential:
                info = os.fstat(credential.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise ApiError('Credential file must be a private regular file (mode 0600).')
                self.__credential = credential.read().strip()
        except ApiError:
            raise
        except Exception:
            raise ApiError('Unable to load the locally configured credential.') from None
        if not self.__credential or any(c.isspace() for c in self.__credential):
            raise ApiError('Credential file must contain one API key, without whitespace.')
        self.__http = urllib.request.build_opener(NoRedirect())

    def _request(self, url, method='GET', data=None, headers=None):
        if not (url.startswith(API_ROOT + '/v1/') or url == MCP_ROOT):
            raise ApiError('Unrecognized API destination.')
        request_headers = {
            'Authorization': 'Bearer ' + self.__credential,
            'Accept': 'application/json',
            'User-Agent': 'TrueHumanityGenerator/1.0',
        }
        request_headers.update(headers or {})
        payload = None
        if data is not None:
            request_headers['Content-Type'] = 'application/json'
            payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
        try:
            with self.__http.open(request, timeout=120) as response:
                body = response.read()
                content_type = response.headers.get('Content-Type', '')
                session = response.headers.get('Mcp-Session-Id')
        except urllib.error.HTTPError as error:
            # Do not echo response bodies, request headers, or credentials.
            raise ApiError(f'API returned HTTP {error.code}.') from None
        except Exception:
            raise ApiError('API connection failed; no request details were logged.') from None
        if not body:
            return {}, session
        try:
            if 'text/event-stream' in content_type:
                messages = [json.loads(line[6:]) for line in body.decode().splitlines() if line.startswith('data: ')]
                return next(message for message in reversed(messages) if 'result' in message or 'error' in message), session
            return json.loads(body), session
        except Exception:
            raise ApiError('API returned an unexpected response format.') from None

    def api(self, path, method='GET', data=None):
        if not path.startswith('/v1/') or '..' in path:
            raise ApiError('Invalid API path.')
        response, _ = self._request(API_ROOT + path, method, data)
        return response

    def authoring_guide(self):
        accept = {'Accept': 'application/json, text/event-stream'}
        initialized, session = self._request(MCP_ROOT, 'POST', {
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                       'clientInfo': {'name': 'true-humanity-authoring', 'version': '1.0'}},
        }, accept)
        if 'error' in initialized:
            raise ApiError('Authoring guide initialization failed.')
        accept['MCP-Protocol-Version'] = initialized['result']['protocolVersion']
        if session:
            accept['Mcp-Session-Id'] = session
        self._request(MCP_ROOT, 'POST', {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, accept)
        guide, _ = self._request(MCP_ROOT, 'POST', {
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'get_authoring_guide', 'arguments': {}},
        }, accept)
        if 'error' in guide or guide.get('result', {}).get('isError'):
            raise ApiError('Authoring guide could not be retrieved.')
        return guide['result']

    def export(self, deck_id, output, export_format):
        job = self.api(f'/v1/decks/{deck_id}/exports', 'POST', {
            'format': export_format, **({'margin': 0, 'slide_number': False,
                                       'slide_notes': False, 'separate_fragments': True}
                                      if export_format == 'pdf' else {}),
        })
        export_id = job['data']['id']
        deadline = time.monotonic() + 600
        while job['data']['status'] == 'pending' and time.monotonic() < deadline:
            time.sleep(min(30, max(1, job.get('meta', {}).get('poll_after_seconds', 5))))
            job = self.api(f'/v1/decks/{deck_id}/exports/{export_id}')
        if job['data']['status'] != 'completed':
            raise ApiError('Export did not complete. Export ID: ' + str(export_id))
        download_url = job['data'].get('download_url')
        if not download_url or urllib.parse.urlsplit(download_url).scheme != 'https':
            raise ApiError('Export did not return a secure download.')
        # Signed download URLs remain inside this process; no API header is sent.
        downloader = urllib.request.build_opener(SecureDownloadRedirect())
        try:
            with downloader.open(download_url, timeout=120) as response:
                asset = response.read()
        except Exception:
            raise ApiError('Unable to download the export; its private URL was not logged.') from None
        if export_format == 'pdf' and not asset.startswith(b'%PDF-'):
            raise ApiError('Downloaded export is not a PDF.')
        if export_format == 'zip' and not asset.startswith(b'PK'):
            raise ApiError('Downloaded export is not a ZIP.')
        output.write_bytes(asset)
        return {'exportId': export_id, 'format': export_format, 'output': str(output), 'bytes': len(asset)}


def prepare_payload(definition):
    """Convert Define API blocks into independently editable REST content blocks."""
    from slides_native import NativeTypography, serialize_rest_text_block
    typography = NativeTypography()
    slides = []
    for slide in definition['slides']:
        markup = [f'<section data-background-color="{slide.get("background-color", "#212121")}" data-transition="none">']
        for index, block in enumerate(slide['blocks']):
            block_id = uuid.uuid5(uuid.NAMESPACE_URL, slide['id'] + ':' + block.get('class', str(index))).hex[:16]
            x, y, width = block['x'], block['y'], block['width']
            if block['type'] == 'shape':
                height = block['height']
                markup.append(
                    f'<div class="sl-block" data-block-type="shape" data-block-id="{block_id}" '
                    f'style="left:{x}px;top:{y}px;width:{width}px;height:{height}px;">'
                    '<div class="sl-block-content" data-shape-stretch="true">'
                    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">'
                    f'<rect width="{width}" height="{height}" fill="{block["fill"]}"/></svg></div></div>')
            elif block['type'] == 'text':
                native = block.get('native-typography')
                if not native or native.get('version')!=typography.config['version']:
                    native = typography.fit(block)
                markup.append(serialize_rest_text_block(block,native,block_id))
            elif block['type'] == 'image':
                height = block['height']
                src = block['src']
                if not src.startswith('http'):
                    origin = os.environ.get('PRESENTATION_ASSET_ORIGIN', '').rstrip('/')
                    if not origin:
                        raise ApiError('Relative image sources require PRESENTATION_ASSET_ORIGIN.')
                    src = f"{origin}/{src.lstrip('/')}"
                markup.append(
                    f'<div class="sl-block" data-block-type="image" data-block-id="{block_id}" '
                    f'style="left:{x}px;top:{y}px;width:{width}px;height:{height}px;">'
                    f'<div class="sl-block-content">'
                    f'<img src="{html.escape(src)}" style="width:100%;height:100%;object-fit:contain;" />'
                    f'</div></div>')
            else:
                raise ApiError('Unsupported block type in the generated definition.')
        markup.append('</section>')
        slides.append({'html': ''.join(markup), 'notes': slide.get('notes', '')})
    return {'title': definition['title'],
            'description': 'Generated presentation with reference-derived layouts. One native editable text block per passage, including its background. League Gothic / Arial font adaptation.',
            'width': definition['width'], 'height': definition['height'], 'margin': 0,
            'visibility': 'self', 'theme_color': 'black-orange', 'theme_font': 'league', 'slides': slides}


def read_receipt(path):
    data = json.loads(path.read_text())
    if not isinstance(data.get('deckId'), int):
        raise ApiError('Invalid local deck receipt.')
    return data


def summarize_deck(deck):
    urls = deck.get('urls', {})
    return {'deckId': deck['id'], 'title': deck['title'], 'slideCount': deck.get('slide_count'),
            'visibility': deck.get('visibility'), 'editUrl': urls.get('edit', deck['url'] + '/edit'),
            'url': deck['url']}


def upload(client, payload_path, receipt_path):
    if receipt_path.exists():
        saved = read_receipt(receipt_path)
        deck = client.api(f'/v1/decks/{saved["deckId"]}')['data']
        return {'existing': True, **summarize_deck(deck)}
    payload = json.loads(payload_path.read_text())
    if payload.get('visibility') != 'self':
        raise ApiError('Uploader only creates private decks.')
    created = client.api('/v1/decks', 'POST', payload)['data']
    # Record the ID immediately; rerunning this command will not create duplicates.
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({'deckId': created['id'], 'payload': str(payload_path),
                                       'slideIds': created.get('slide_ids', []),
                                       'expectedSlideCount': len(payload['slides'])}, indent=2) + '\n')
    deck = client.api(f'/v1/decks/{created["id"]}')['data']
    receipt = {**read_receipt(receipt_path), **summarize_deck(deck)}
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    if deck.get('slide_count') != len(payload['slides']):
        raise ApiError('Created deck needs review: slide count differs from the payload.')
    return {'created': True, **summarize_deck(deck)}


def combine_backgrounds(client, receipt_path, backup_dir):
    """Repair known background/text pairs in place, preserving current text edits."""
    from lxml import html as dom

    def parse(markup):
        return dom.fromstring(markup)

    def blocks(root):
        return {b.get('data-block-id'): b for b in root.xpath('.//*[@data-block-type]')}

    def css(node):
        return dict(part.split(':', 1) for part in node.get('style', '').split(';') if ':' in part)

    def set_css(node, changes):
        style = {key.strip(): value.strip() for key, value in css(node).items()}
        for key,value in changes.items():
            if value is None: style.pop(key,None)
            else: style[key]=value
        node.set('style', ';'.join(key+':'+value for key,value in style.items())+';')

    def bounds(node):
        style = {key.strip(): value.strip() for key, value in css(node).items()}
        return tuple(float(style[key].removesuffix('px')) for key in ('left','top','width','height'))

    def content(node):
        matches = node.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," sl-block-content ")]')
        if len(matches) != 1:
            raise ApiError('Unexpected block structure; no slide was changed.')
        return matches[0]

    def text_signature(root):
        return {bid: [dom.tostring(child, encoding='unicode') for child in content(b)]
                for bid,b in blocks(root).items() if b.get('data-block-type') == 'text'}

    receipt = read_receipt(receipt_path)
    original = json.loads(Path(receipt['payload']).read_text())
    pairs = []
    for slide in original['slides']:
        ordered = list(blocks(parse(slide['html'])).values())
        for index, b in enumerate(ordered[:-1]):
            if b.get('data-block-type') == 'shape' and ordered[index+1].get('data-block-type') == 'text':
                pairs.append((b.get('data-block-id'), ordered[index+1].get('data-block-id')))
    if not pairs:
        raise ApiError('Original payload has no background/text pairs; use its saved backup.')
    deck_id = receipt['deckId']
    slide_ids = receipt.get('slideIds') or [s['id'] for s in client.api(f'/v1/decks/{deck_id}/slides?per_page=200')['data']]
    snapshots = [client.api(f'/v1/decks/{deck_id}/slides/{sid}')['data'] for sid in slide_ids]
    backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    (backup_dir/'original-payload.json').write_text(json.dumps(original, ensure_ascii=False, indent=2)+'\n')
    (backup_dir/'slides-before.json').write_text(json.dumps(snapshots, ensure_ascii=False, indent=2)+'\n')
    merged_count = changed_slides = 0
    for sid, snapshot in zip(slide_ids, snapshots):
        endpoint = f'/v1/decks/{deck_id}/slides/{sid}'
        # Re-read immediately before writing so edits since the snapshot survive.
        current = client.api(endpoint)['data']
        root = parse(current['html'])
        before = text_signature(root)
        existing = blocks(root)
        changed_ids = []
        for shape_id, text_id in pairs:
            shape, text = existing.get(shape_id), existing.get(text_id)
            if shape is None or text is None:
                continue
            rects = shape.xpath('.//rect')
            if len(rects) != 1:
                raise ApiError('Background has been redesigned; automatic merge stopped.')
            text_content = content(text)
            sx,sy,sw,sh = bounds(shape)
            tx,ty,tw,th = bounds(text)
            x,y = min(sx,tx),min(sy,ty)
            right,bottom = max(sx+sw,tx+tw),max(sy+sh,ty+th)
            fill = {k.strip():v.strip() for k,v in css(text_content).items()}.get('background-color')
            if not fill or fill in ('transparent','rgba(0, 0, 0, 0)'):
                fill = rects[0].get('fill')
            if not fill:
                raise ApiError('Background color is missing; automatic merge stopped.')
            set_css(text, {'left':f'{x:g}px','top':f'{y:g}px','width':f'{right-x:g}px','height':f'{bottom-y:g}px'})
            text.set('data-text-layout', 'fixed')
            # Natural content sizing is required for native Auto-fit growth.
            # This legacy merge helper must not reintroduce inner 100% sizing.
            set_css(text_content, {'background-color':fill,'width':None,'height':None,
                                  'box-sizing':'border-box',
                                  'padding':f'{ty-y:g}px {right-tx-tw:g}px {bottom-ty-th:g}px {tx-x:g}px'})
            shape.getparent().remove(shape)
            changed_ids.append(text_id)
        if not changed_ids:
            continue
        if before != text_signature(root):
            raise ApiError('Text preservation check failed; this slide was not changed.')
        markup = dom.tostring(root, encoding='unicode')
        (backup_dir/f'slide-{sid}-before.html').write_text(current['html'])
        (backup_dir/f'slide-{sid}-after.html').write_text(markup)
        client.api(endpoint, 'PATCH', {'html': markup})
        returned = client.api(endpoint)['data']
        result = parse(returned['html'])
        if before != text_signature(result) or returned.get('notes') != current.get('notes'):
            raise ApiError('Server preservation check failed; see the saved slide backup.')
        after_blocks = blocks(result)
        if any('background-color' not in content(after_blocks[bid]).get('style','') for bid in changed_ids):
            raise ApiError('Server did not retain a text background; see the saved backup.')
        merged_count += len(changed_ids)
        changed_slides += 1
        print(json.dumps({'updatedSlide':sid,'combinedTextBoxes':len(changed_ids)}), flush=True)
    return {'deckId':deck_id,'updatedSlides':changed_slides,'combinedTextBoxes':merged_count,
            'textAndNotesPreserved':True,'backup':str(backup_dir)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('account')
    guide = commands.add_parser('authoring-guide')
    guide.add_argument('--output', type=Path, required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--definition', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
    send = commands.add_parser('upload')
    send.add_argument('--payload', type=Path, required=True)
    send.add_argument('--receipt', type=Path, required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--receipt', type=Path, required=True)
    inspect.add_argument('--html-output', type=Path)
    combine = commands.add_parser('combine-backgrounds')
    combine.add_argument('--receipt', type=Path, required=True)
    combine.add_argument('--backup-dir', type=Path, required=True)
    export = commands.add_parser('export')
    export.add_argument('--receipt', type=Path, required=True)
    export.add_argument('--format', choices=['pdf', 'zip'], default='pdf')
    export.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        definition = json.loads(args.definition.read_text())
        payload = prepare_payload(definition)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'preparedSlides': len(payload['slides']), 'fonts': ['League Gothic','Arial'], 'shapeBlocks':sum(s['html'].count('data-block-type="shape"') for s in payload['slides']), 'visibility': 'self'}))
        return
    client = SlidesClient()
    if args.command == 'account':
        user = client.api('/v1/user')['data']
        print(json.dumps({'connected': True, 'username': user['username'],
                          'accountType': user.get('account', {}).get('type')}, indent=2))
    elif args.command == 'authoring-guide':
        args.output.write_text(json.dumps(client.authoring_guide(), ensure_ascii=False, indent=2) + '\n')
        print('Saved non-secret authoring guidance to ' + str(args.output))
    elif args.command == 'upload':
        print(json.dumps(upload(client, args.payload, args.receipt), indent=2))
    elif args.command == 'inspect':
        receipt = read_receipt(args.receipt)
        deck = client.api(f'/v1/decks/{receipt["deckId"]}?include_deck_html=true')['data']
        markup = deck.get('deck_html', '')
        if args.html_output:
            args.html_output.write_text(markup)
        result = {**summarize_deck(deck),
                  'textBlocks': len(re.findall(r'data-block-type="text"', markup)),
                  'shapeBlocks': len(re.findall(r'data-block-type="shape"', markup))}
        print(json.dumps(result, indent=2))
    elif args.command == 'combine-backgrounds':
        print(json.dumps(combine_backgrounds(client, args.receipt, args.backup_dir), indent=2))
    elif args.command == 'export':
        receipt = read_receipt(args.receipt)
        print(json.dumps(client.export(receipt['deckId'], args.output, args.format), indent=2))


if __name__ == '__main__':
    try:
        main()
    except ApiError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Operation failed; no response bodies or credentials were logged.', file=sys.stderr)
        sys.exit(1)
