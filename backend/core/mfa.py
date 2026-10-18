"""Optional TOTP MFA for platform administrators."""
import base64
import hashlib
import hmac
import struct
import time

from django.utils.crypto import get_random_string


def generate_mfa_secret():
    return get_random_string(32, allowed_chars='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567')


def _hotp(secret, counter, digits=6):
    key = base64.b32decode(secret.upper() + '=' * ((8 - len(secret) % 8) % 8), casefold=True)
    msg = struct.pack('>Q', counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f'{code:0{digits}d}'


def verify_totp(secret, token, window=1, step=30):
    if not secret or not token:
        return False
    token = str(token).strip()
    counter = int(time.time()) // step
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret, counter + offset), token):
            return True
    return False
