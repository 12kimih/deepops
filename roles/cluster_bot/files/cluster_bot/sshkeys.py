"""SSH public keys a person registers: checked, and shown by fingerprint.

    parse(line) -> Key(kind, blob, comment, fingerprint, bits)   or Invalid(why)

Accepted: ssh-ed25519, the hardware-backed sk-ssh-ed25519@openssh.com and
sk-ecdsa-sha2-nistp256@openssh.com, and ssh-rsa of at least MIN_RSA_BITS. A private key
pasted by mistake is refused with a warning to replace it. The fingerprint is
OpenSSH's (SHA256:<base64, no padding> of the key blob), as ssh-keygen -l prints it.
"""

import base64
import binascii
import hashlib
import struct
from dataclasses import dataclass

MIN_RSA_BITS = 3072
KINDS = {"ssh-ed25519", "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com", "ssh-rsa"}


class Invalid(ValueError):
    """Not a key we accept; str() says why, in the person's language."""


@dataclass
class Key:
    kind: str
    blob: bytes
    comment: str
    fingerprint: str
    bits: int

    def line(self):
        return f"{self.kind} {base64.b64encode(self.blob).decode()} {self.comment}".rstrip()


def _strings(blob):
    """The length-prefixed fields of an SSH key blob."""
    out, i = [], 0
    while i < len(blob):
        if i + 4 > len(blob):
            raise Invalid("공개키 형식이 올바르지 않습니다")
        (n,) = struct.unpack(">I", blob[i : i + 4])
        out.append(blob[i + 4 : i + 4 + n])
        i += 4 + n
    return out


def parse(line):
    text = (line or "").strip()
    if "PRIVATE KEY" in text:
        raise Invalid(
            "개인키입니다. 개인키는 절대 보내지 마세요: 새로 만들고(ssh-keygen) .pub 파일 내용만 붙여 넣어 주세요"
        )
    parts = text.split(None, 2)
    if len(parts) < 2 or parts[0] not in KINDS:
        raise Invalid(f"받지 않는 키 종류입니다 (가능: {', '.join(sorted(KINDS))})")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        raise Invalid("공개키 형식이 올바르지 않습니다") from None
    fields = _strings(blob)
    if not fields or fields[0].decode(errors="replace") != parts[0]:
        raise Invalid("키 종류와 내용이 맞지 않습니다")
    bits = 256
    if parts[0] == "ssh-rsa":
        bits = int.from_bytes(fields[2], "big").bit_length() if len(fields) >= 3 else 0
        if bits < MIN_RSA_BITS:
            raise Invalid(
                f"RSA 키가 {bits}비트입니다: RSA 키는 {MIN_RSA_BITS}비트 이상이어야 합니다 (ed25519 키도 됩니다)"
            )
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return Key(parts[0], blob, parts[2] if len(parts) > 2 else "", f"SHA256:{digest}", bits)
