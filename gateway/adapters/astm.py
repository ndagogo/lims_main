"""ASTM E1394 adapter skeleton (PDF §13.3 contract).

Implements framing helpers and a parse stub. Vendor profiles are certified
separately; this module is the shared contract surface for the Windows gateway.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


STX = b'\x02'
ETX = b'\x03'
ETB = b'\x17'
CR = b'\x0d'
LF = b'\x0a'
ACK = b'\x06'
NAK = b'\x15'
ENQ = b'\x05'
EOT = b'\x04'


@dataclass
class AstmRecord:
    record_type: str
    fields: list[str] = field(default_factory=list)


@dataclass
class AstmMessage:
    records: list[AstmRecord] = field(default_factory=list)
    sample_id: str = ''
    instrument_id: str = ''
    results: list[dict] = field(default_factory=list)


def checksum(frame_body: bytes) -> str:
    total = sum(frame_body) % 256
    return f'{total:02X}'


def frame(sequence: int, content: str, *, final: bool = True) -> bytes:
    end = ETX if final else ETB
    body = f'{sequence % 8}{content}'.encode('ascii', errors='replace') + end
    cs = checksum(body)
    return STX + body + cs.encode('ascii') + CR + LF


def parse_records(text: str) -> AstmMessage:
    """Parse pipe-delimited ASTM text into a structured message stub."""
    msg = AstmMessage()
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = line.strip()
        if not line or '|' not in line:
            continue
        parts = line.split('|')
        rec = AstmRecord(record_type=parts[0][:1] or '?', fields=parts)
        msg.records.append(rec)
        if rec.record_type == 'H' and len(parts) > 4:
            msg.instrument_id = parts[4]
        if rec.record_type == 'O' and len(parts) > 2:
            msg.sample_id = re.sub(r'\^.*', '', parts[2])
        if rec.record_type == 'R' and len(parts) > 3:
            msg.results.append({
                'test_code': parts[2],
                'value': parts[3],
                'units': parts[4] if len(parts) > 4 else '',
            })
    return msg


def to_ingest_payload(message: AstmMessage, *, analyzer_id: str, external_message_id: str) -> dict:
    return {
        'analyzer_id': analyzer_id,
        'external_message_id': external_message_id,
        'raw_payload': '\n'.join('|'.join(r.fields) for r in message.records),
        'parsed_hint': {
            'protocol': 'ASTM',
            'sample_id': message.sample_id,
            'instrument_id': message.instrument_id,
            'results': message.results,
        },
    }
