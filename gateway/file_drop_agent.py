"""Minimal local file-drop bridge for instruments that export HL7 text files.

This agent does not open analyzer TCP/serial ports. Configure the analyzer middleware to
write completed messages into INBOX_DIR. Use a dedicated API service account and HTTPS.
"""
import json
import os
import shutil
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get('HFDN_API_BASE', 'https://hfdn.example.org/api').rstrip('/')
API_TOKEN = os.environ.get('HFDN_API_TOKEN', '')
ANALYZER_ID = os.environ.get('HFDN_ANALYZER_ID', '')
INBOX_DIR = Path(os.environ.get('HFDN_INBOX_DIR', r'C:\HFDN-Gateway\inbox'))
PROCESSED_DIR = Path(os.environ.get('HFDN_PROCESSED_DIR', r'C:\HFDN-Gateway\processed'))
FAILED_DIR = Path(os.environ.get('HFDN_FAILED_DIR', r'C:\HFDN-Gateway\failed'))
POLL_SECONDS = max(2, int(os.environ.get('HFDN_POLL_SECONDS', '5')))


def send_message(path: Path) -> None:
    if not API_TOKEN or not ANALYZER_ID:
        raise RuntimeError('Set HFDN_API_TOKEN and HFDN_ANALYZER_ID in the service environment.')
    payload = path.read_text(encoding='utf-8', errors='replace')
    if not payload.strip():
        raise ValueError('Empty analyzer message file.')
    body = json.dumps({
        'analyzer_id': ANALYZER_ID,
        'external_message_id': path.name,
        'raw_payload': payload,
    }).encode('utf-8')
    request = urllib.request.Request(
        f'{API_BASE}/analyzer-messages/ingest/', data=body,
        headers={'Authorization': f'Bearer {API_TOKEN}', 'Content-Type': 'application/json'},
        method='POST',
    )
    # Default SSL context verifies the server certificate. Never disable certificate checks.
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=20) as response:
        if response.status not in (200, 201, 202):
            raise RuntimeError(f'API returned HTTP {response.status}')


def main() -> None:
    for directory in (INBOX_DIR, PROCESSED_DIR, FAILED_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    print(f'HFDN file-drop gateway started; inbox={INBOX_DIR}')
    while True:
        for path in sorted(INBOX_DIR.glob('*')):
            if not path.is_file() or path.suffix.lower() not in {'.hl7', '.txt', '.dat'}:
                continue
            # Rename to a processing suffix to reduce duplicate reads by concurrent runs.
            working = path.with_suffix(path.suffix + '.processing')
            try:
                path.rename(working)
            except OSError:
                continue
            try:
                send_message(working)
                shutil.move(str(working), str(PROCESSED_DIR / path.name))
                print(f'Accepted {path.name}')
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
                target = FAILED_DIR / path.name
                shutil.move(str(working), str(target))
                # Keep logs minimal; never print raw clinical payloads or tokens.
                print(f'Failed {path.name}: {type(exc).__name__}: {exc}')
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
