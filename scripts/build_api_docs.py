#!/usr/bin/env python3
"""Build the markdown guide, standalone site, and downloadable integration pack.

Install docs-only tooling: python -m pip install -r scripts/requirements-docs.txt
Run: python scripts/build_api_docs.py [--check]
The frontend Docker build needs no Python; generated static files are committed.
"""
import argparse
import io
import json
from pathlib import Path
import re
import zipfile

import markdown

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/api'
PUBLIC = ROOT / 'frontend/public/integration'

CSS = '''
article{overflow-wrap:anywhere}
:root{color-scheme:light;--ink:#15263b;--muted:#526478;--line:#dce4ee;--accent:#185ad6;--bg:#f5f8fc}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:28px}body{margin:0;font:16px/1.65 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--accent);text-underline-offset:3px}header{background:#112640;color:white;padding:36px max(24px,calc((100vw - 1320px)/2));border-bottom:4px solid #72b0ff}header .eyebrow{color:#a9c9f5;font-size:12px;letter-spacing:.15em;text-transform:uppercase;font-weight:700}header .title{font-size:clamp(26px,4vw,36px);font-weight:750;line-height:1.2;margin:8px 0}header p{color:#cfdaeb;margin:12px 0 20px;max-width:750px}.downloads{display:flex;gap:10px;flex-wrap:wrap}.downloads a,.downloads button{font:inherit;font-size:13px;font-weight:650;color:white;border:1px solid #6c87a9;border-radius:6px;padding:8px 12px;background:transparent;text-decoration:none;cursor:pointer}.downloads a:hover,.downloads button:hover{background:#274262}
.layout{display:grid;grid-template-columns:265px minmax(0,1fr);gap:40px;max-width:1320px;padding:36px 24px 80px;margin:auto}aside{position:sticky;top:24px;align-self:start;max-height:calc(100vh - 48px);overflow:auto;padding-right:12px;font-size:13px}aside summary{font-size:12px;font-weight:750;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}aside ul{list-style:none;padding:0}aside li{margin:8px 0}aside a{text-decoration:none;color:var(--muted);display:block}aside a:hover{color:var(--accent)}aside li ul{display:none}article{min-width:0;background:white;padding:36px 42px;border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 28px #243c5710}article>h1{font-size:30px;line-height:1.2;margin:0 0 18px}h2{font-size:25px;line-height:1.3;margin:54px 0 18px;padding-top:16px;border-top:1px solid var(--line)}h3{font-size:19px;line-height:1.5;margin-top:32px}p{margin:14px 0}li{margin:6px 0}strong{font-weight:700}code{font:13px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;background:#edf2f8;border-radius:4px;padding:2px 5px;overflow-wrap:anywhere}pre{position:relative;background:#142438;color:#e7edf7;padding:20px;border-radius:7px;overflow:auto;max-height:520px;border:1px solid #243d59}pre code{padding:0;background:none;color:inherit;white-space:pre;overflow-wrap:normal}.copy{position:sticky;float:right;top:0;right:0;background:#2c4663;color:white;border:1px solid #6c87a9;border-radius:4px;font-size:11px;padding:4px 8px;cursor:pointer}.table-wrap{overflow-x:auto;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.55}th,td{border:1px solid var(--line);padding:11px 13px;vertical-align:top;text-align:left}th{background:#edf3fb;font-weight:700}tr:nth-child(even){background:#fafcfe}blockquote{margin:20px 0;padding:12px 20px;border-left:3px solid var(--accent);background:#edf4ff}.footer{font-size:12px;color:var(--muted);margin-top:40px}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #72b0ff;outline-offset:3px}
@media(max-width:980px){.layout{display:block;padding:20px 12px}aside{position:static;max-height:none;padding:0 10px 20px}aside details:not([open]){margin:0}article{padding:24px 20px}h2{font-size:22px}header{padding:28px 24px}}@media print{header,aside,.copy{display:none}.layout{display:block;padding:0}article{border:0;box-shadow:none;padding:0}body{background:white;font-size:10pt}h2{break-after:avoid}pre{max-height:none;overflow:visible;white-space:pre-wrap;background:#f4f6f9;color:#111}pre code{white-space:pre-wrap;overflow-wrap:anywhere}.table-wrap{overflow:visible}table{font-size:9pt}tr{break-inside:avoid}a{color:inherit}}
'''
JS = '''
for (const pre of document.querySelectorAll('pre')) {
 const code=pre.querySelector('code'); if(!code) continue;
 const button=document.createElement('button'); button.className='copy'; button.textContent='Copy'; button.setAttribute('aria-label','Copy code example');
 button.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(code.textContent);button.textContent='Copied';}catch{button.textContent='Select and copy';}setTimeout(()=>button.textContent='Copy',2000);});pre.prepend(button);
}
if(window.matchMedia('(max-width:980px)').matches)document.querySelector('aside details').removeAttribute('open');
document.querySelector('#print-guide').addEventListener('click',()=>window.print());
'''


def render():
    examples = json.loads((SOURCE / 'examples.json').read_text())
    source = (SOURCE / 'guide.md').read_text()
    def example(match):
        return '```json\n' + json.dumps(examples[match.group(1)], indent=2, ensure_ascii=False) + '\n```'
    guide = re.sub(r'\{\{example:([a-z_]+)\}\}', example, source)
    assert '{{example:' not in guide
    md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc'], extension_configs={'toc': {'toc_depth': '2-2'}})
    body = md.convert(guide)
    body = body.replace('<table>', '<div class="table-wrap"><table>').replace('</table>', '</table></div>')
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Marker — Developer Integration Guide</title><meta name="description" content="Complete AI Marker API inputs, outputs, marking workflows, errors, and executable integration examples.">
<style>{CSS}</style></head><body>
<header><div class="eyebrow">Developer documentation · Reviewed 20 September 2026</div><div class="title">Build AI marking into your application</div>
<p>Upload answer sheets, retrieve weighted results, and keep human review in control. A complete contract guide for the client engineering team.</p>
<div class="downloads"><a href="integration-pack.zip" download>Download integration pack</a><a href="API.md" download>Markdown</a><a href="openapi.json" download>OpenAPI snapshot</a><a href="integration_client.py" download>Python client</a><button id="print-guide">Print / save PDF</button></div></header>
<div class="layout"><aside aria-label="On this page"><details open><summary>On this page</summary>{md.toc}</details></aside><article>{body}<div class="footer">Generated from the repository's integration guide and tested synthetic API examples.</div></article></div>
<script>{JS}</script></body></html>'''
    files = {
        'index.html': html.encode(), 'API.md': guide.encode(),
        'examples.json': (SOURCE / 'examples.json').read_bytes(),
        'openapi.json': (SOURCE / 'openapi.json').read_bytes(),
        'integration_client.py': (SOURCE / 'integration_client.py').read_bytes(),
    }
    for name, data in examples.items():
        files[f'examples/{name}.json'] = (json.dumps(data, indent=2, ensure_ascii=False)+'\n').encode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as pack:
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 9, 9, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            pack.writestr(entry, content)
    files['integration-pack.zip'] = archive.getvalue()
    return guide, files


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args=parser.parse_args()
    guide, files=render()
    outputs={ROOT/'docs/API.md': guide.encode(), **{PUBLIC/name:data for name,data in files.items()}}
    if args.check:
        stale=[str(path.relative_to(ROOT)) for path,data in outputs.items() if not path.exists() or path.read_bytes()!=data]
        if stale:
            raise SystemExit('Regenerate API documentation: '+', '.join(stale))
        print(f'{len(outputs)} documentation artifacts are current.')
        return
    for path,data in outputs.items():
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
    print(f'Built {len(outputs)} documentation artifacts from docs/api/guide.md.')


if __name__=='__main__':
    main()
