#!/usr/bin/env python3
"""Upload an exam PDF, poll, and save extracted and marked results.

Requires: pip install requests
Credentials: API_BASE and API_KEY environment variables.
Resume: python integration_client.py --submission-id 123 --output marked.json
POST requests are deliberately never retried automatically.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

import requests

TRANSIENT_STATUS = {429, 502, 503, 504}


class MultipartPDF:
    """Sized iterable: requests streams file chunks instead of buffering files=."""
    def __init__(self, pdf, filename):
        self.pdf = pdf
        self.boundary = 'aimarker-' + uuid.uuid4().hex
        filename = filename.replace('\\', '_').replace('"', '_').replace('\r', '_').replace('\n', '_')
        self.prefix = (f'--{self.boundary}\r\nContent-Disposition: form-data; name="file"; '
                       f'filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n').encode()
        self.suffix = f'\r\n--{self.boundary}--\r\n'.encode()
        self.size = os.fstat(pdf.fileno()).st_size

    def __len__(self):
        return len(self.prefix) + self.size + len(self.suffix)

    def __iter__(self):
        yield self.prefix
        while chunk := self.pdf.read(1024 * 1024):
            yield chunk
        yield self.suffix


class MarkerClient:
    def __init__(self, base_url, api_key='', wait_seconds=1800, poll_interval=3):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        if api_key:
            self.session.headers['X-API-Key'] = api_key
        self.wait_seconds = wait_seconds
        self.poll_interval = poll_interval

    def get(self, path, deadline):
        """Retry read-only requests, bounded by the workflow deadline."""
        backoff = 1
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Polling deadline reached; resume using the saved submission ID.')
            try:
                response = self.session.get(
                    self.base_url + path,
                    timeout=(min(10, remaining), min(30, remaining)),
                )
                if response.status_code not in TRANSIENT_STATUS:
                    response.raise_for_status()
                    return response.json()
            except (requests.Timeout, requests.ConnectionError):
                pass
            time.sleep(min(backoff, max(0, deadline - time.monotonic())))
            backoff = min(backoff * 2, 15)

    def upload(self, pdf_path, template_id=None, idempotency_key=None):
        self.last_upload_key = idempotency_key or uuid.uuid4().hex
        with Path(pdf_path).open('rb') as pdf:
            body = MultipartPDF(pdf, Path(pdf_path).name)
            response = self.session.post(
                self.base_url + '/upload',
                data=body,
                headers={'Content-Type': f'multipart/form-data; boundary={body.boundary}',
                         'Idempotency-Key': self.last_upload_key},
                params={'template_id': template_id} if template_id else None,
                timeout=(15, 21600),
            )
        response.raise_for_status()
        return response.json()['submission_id']

    def collect(self, submission_id, output):
        deadline = time.monotonic() + self.wait_seconds
        previous = None
        while True:
            state = self.get(f'/status/{submission_id}', deadline)
            status = state['status']
            if status != previous:
                print(f'Submission {submission_id}: {status}', file=sys.stderr)
                previous = status
            if status == 'completed':
                break
            if status in {'failed', 'cancelled'}:
                raise RuntimeError(f"Submission {submission_id}: {status}; {state.get('error_message') or 'no completed-result guarantee'}")
            if status not in {'pending', 'processing'}:
                raise RuntimeError(f'Unknown submission state: {status}')
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))

        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        extracted = self.get(f'/submission/{submission_id}', deadline)
        extracted_path = output.with_name(output.stem + '.extracted.json')
        extracted_path.write_text(json.dumps(extracted, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Current extracted answers saved to {extracted_path}', file=sys.stderr)

        while True:
            marking = self.get(f'/submission/{submission_id}/marking', deadline)
            run = marking.get('latest_run')
            if not run or run['status'] != 'processing':
                break
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        if not run or run['status'] != 'completed':
            state = run['status'] if run else 'not started'
            raise RuntimeError(f'Marking is {state}; extracted answers were retained. Inspect /submission/{submission_id}/marking before retrying.')

        # A 409 here can mean partial candidate coverage, despite completed marking.
        marked = self.get(f'/submission/{submission_id}/marked-json', deadline)
        output.write_text(json.dumps(marked, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        review_count = sum(
            outcome['status'] == 'needs_review'
            for candidate in marked['candidates']
            for outcome in candidate['marking']['outcomes']
        )
        print(f'Saved {output}; {review_count} question(s) need human review.', file=sys.stderr)
        return marked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', nargs='?', type=Path)
    parser.add_argument('--submission-id', type=int)
    parser.add_argument('--idempotency-key', help='Reuse the saved upload key to reconcile an interrupted upload')
    parser.add_argument('--template-id', help='Optional forced layout; omit for per-page detection')
    parser.add_argument('--output', type=Path, default=Path('marked.json'))
    parser.add_argument('--wait-seconds', type=float, default=1800)
    parser.add_argument('--poll-interval', type=float, default=3)
    args = parser.parse_args()
    if (args.pdf is None) == (args.submission_id is None):
        parser.error('Provide exactly one PDF path or --submission-id.')
    if args.wait_seconds <= 0 or args.poll_interval <= 0:
        parser.error('Timeout and polling interval must be positive.')
    client = MarkerClient(
        os.environ.get('API_BASE', 'https://aimarker-bk.seamo-official.org'),
        os.environ.get('API_KEY', ''), args.wait_seconds, args.poll_interval,
    )
    submission_id = args.submission_id
    try:
        if submission_id is None:
            upload_key = args.idempotency_key or uuid.uuid4().hex
            receipt = args.output.with_name(args.output.stem + '.upload.json')
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps({'idempotency_key': upload_key, 'api_base': client.base_url,
                                          'pdf': str(args.pdf.resolve())}) + '\n', encoding='utf-8')
            print(f'Upload receipt saved to {receipt}.', file=sys.stderr)
            submission_id = client.upload(args.pdf, args.template_id, upload_key)
            state_path = args.output.with_name(args.output.stem + '.task.json')
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({'submission_id': submission_id, 'api_base': client.base_url}) + '\n', encoding='utf-8')
            print(f'Accepted task {submission_id}. Resume with --submission-id {submission_id}.', file=sys.stderr)
        client.collect(submission_id, args.output)
    except (requests.RequestException, OSError, TimeoutError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        if submission_id is not None:
            print(f'Task ID: {submission_id}. Resume polling; do not blindly upload again.', file=sys.stderr)
        else:
            print('Upload outcome may be unknown. Reconcile with the saved --idempotency-key before retrying.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
