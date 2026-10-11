"""Upload checks: magic bytes, size, and optional ClamAV INSTREAM scanning."""
import os
import socket

from django.core.exceptions import ValidationError

_KINDS = {
    'pdf': (b'%PDF',),
    'jpeg': (b'\xff\xd8\xff',),
    'png': (b'\x89PNG\r\n\x1a\n',),
}


def _required_scanner():
    return os.getenv('CLAMAV_REQUIRED', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def scan_malware(file_obj):
    host = os.getenv('CLAMAV_HOST', '').strip()
    if not host:
        if _required_scanner():
            raise ValidationError('Malware scanning is required but CLAMAV_HOST is not configured.')
        return
    port = int(os.getenv('CLAMAV_PORT', '3310'))
    file_obj.seek(0)
    payload = file_obj.read()
    file_obj.seek(0)
    try:
        sock = socket.create_connection((host, port), timeout=15)
    except OSError as exc:
        raise ValidationError('Malware scanning is unavailable.') from exc
    try:
        sock.sendall(b'zINSTREAM\0')
        view = memoryview(payload)
        step = 64 * 1024
        for offset in range(0, len(view), step):
            chunk = view[offset:offset + step].tobytes()
            sock.sendall(len(chunk).to_bytes(4, 'big') + chunk)
        sock.sendall(b'\x00\x00\x00\x00')
        reply = b''
        while True:
            part = sock.recv(4096)
            if not part:
                break
            reply += part
            if b'\0' in reply:
                break
    finally:
        sock.close()
    if b'FOUND' in reply:
        raise ValidationError('The file was rejected by malware scanning.')
    if b'OK' not in reply:
        raise ValidationError('Malware scanning did not accept the file.')


def validate_upload(file_obj, kinds=('pdf', 'jpeg', 'png'), max_bytes=10 * 1024 * 1024):
    size = getattr(file_obj, 'size', None)
    if size is not None and size > max_bytes:
        raise ValidationError(f'File must not exceed {max_bytes // (1024 * 1024)} MB.')
    file_obj.seek(0)
    head = file_obj.read(16)
    file_obj.seek(0)
    allowed = [sig for kind in kinds for sig in _KINDS[kind]]
    if not any(head.startswith(sig) for sig in allowed):
        raise ValidationError('File content must be a PDF, JPEG, or PNG.')
    scan_malware(file_obj)


def validate_imaging_file(file_obj, max_bytes=25 * 1024 * 1024):
    """Accept DICOM, JPEG, PNG, or PDF imaging uploads. Returns the stored kind."""
    size = getattr(file_obj, 'size', None)
    if size is not None and size > max_bytes:
        raise ValidationError('Imaging file must not exceed 25 MB.')
    file_obj.seek(0)
    head = file_obj.read(132)
    file_obj.seek(0)
    if head.startswith(b'\xff\xd8\xff'):
        kind = 'JPEG'
    elif head.startswith(b'\x89PNG\r\n\x1a\n'):
        kind = 'PNG'
    elif head.startswith(b'%PDF'):
        kind = 'PDF'
    elif len(head) >= 132 and head[128:132] == b'DICM':
        kind = 'DICOM'
    else:
        raise ValidationError('Upload a DICOM, JPEG, PNG, or PDF file.')
    scan_malware(file_obj)
    return kind
