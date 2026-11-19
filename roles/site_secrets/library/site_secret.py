#!/usr/bin/python
"""Create or adopt one secret file of the site config if it is missing; never replace one."""

DOCUMENTATION = r"""
module: site_secret
short_description: Create a missing secret file (random bytes or a GPG signing key pair)
description:
  - Runs on the controller. Does nothing when the file exists, so a site keeps its keys.
  - With I(adopt_from), writes I(content) (a host's installed copy) instead of a new
    secret, after checking it against I(sha256).
  - Secret material never reaches disk unencrypted when I(vault) is true (ansible-vault
    reads it on stdin), and is never returned.
  - Supports check mode (reports what it would create).
options:
  path:
    description: The secret file (for C(gpg_signing), the armored secret key).
    type: path
    required: true
  kind:
    description: C(random) bytes from the OS CSPRNG, or a C(gpg_signing) key pair.
    type: str
    choices: [random, gpg_signing]
    required: true
  length:
    description: Bytes of a C(random) secret.
    type: int
    default: 1024
  uid:
    description: User ID of a C(gpg_signing) key.
    type: str
  public_path:
    description: Where a C(gpg_signing) key's armored public key goes (never vaulted).
    type: path
  vault:
    description: Vault-encrypt I(path) with I(vault_password_file).
    type: bool
    default: true
  vault_password_file:
    description: The repository's vault password file (or script).
    type: path
  ansible_vault:
    description: The ansible-vault of the Ansible running the play.
    type: path
    default: ansible-vault
  mode:
    description: Mode of the files written.
    type: raw
    default: "0600"
  adopt_from:
    description: The host whose installed copy is adopted (for the result only).
    type: str
  content:
    description: That copy, base64 (as slurp returns it); not needed in check mode.
    type: str
  sha256:
    description: The checksum the hosts agreed on; I(content) must match it.
    type: str
"""

import base64
import hashlib
import os
import secrets
import shutil
import subprocess
import tempfile

from ansible.module_utils.basic import AnsibleModule


def gpg_pair(module, uid):
    """(armored secret key, armored public key) of a new passphrase-less ed25519
    signing key, made in a throwaway GNUPGHOME."""
    home = tempfile.mkdtemp(prefix="site-secret-gpg-")
    env = dict(os.environ, GNUPGHOME=home, LC_ALL="C")

    def run(*args):
        rc, out, err = module.run_command(["gpg", "--batch", "--quiet", *args], environ_update=env)
        if rc != 0:  # stderr only: stdout may hold the key
            module.fail_json(msg=f"gpg {' '.join(args)} failed: {err.strip()}")
        return out

    try:
        run("--passphrase", "", "--quick-gen-key", uid, "ed25519", "sign", "never")
        secret = run("--armor", "--export-secret-keys")
        public = run("--armor", "--export")
        if "PRIVATE KEY BLOCK" not in secret or "PUBLIC KEY BLOCK" not in public:
            module.fail_json(msg="gpg exported no key")
        return secret, public
    finally:
        module.run_command(["gpgconf", "--kill", "all"], environ_update=env)
        shutil.rmtree(home, ignore_errors=True)


def write_new(module, path, data, mode, vault):
    """Write data to path only if path does not exist: into a private temporary file
    beside it, then hard-linked into place, which fails rather than replace a file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".site-secret-")
    try:
        os.close(fd)
        if vault:
            p = module.params
            env = dict(os.environ, ANSIBLE_VAULT_PASSWORD_FILE=p["vault_password_file"])
            env.pop("ANSIBLE_VAULT_IDENTITY_LIST", None)
            proc = subprocess.run(
                [p["ansible_vault"], "encrypt", "--output", tmp, "-"],
                input=data,
                env=env,
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                module.fail_json(
                    msg=f"ansible-vault encrypt failed: {proc.stderr.decode(errors='replace').strip()}"
                )
        else:
            with open(tmp, "wb") as f:
                f.write(data)
        os.chmod(tmp, mode)
        os.link(tmp, path)
    except FileExistsError:
        module.fail_json(msg=f"{path} appeared while it was being created; left it alone")
    finally:
        os.unlink(tmp)


def main():
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "required": True},
            "kind": {"type": "str", "required": True, "choices": ["random", "gpg_signing"]},
            "length": {"type": "int", "default": 1024},
            "uid": {"type": "str"},
            "public_path": {"type": "path"},
            "vault": {"type": "bool", "default": True},
            "vault_password_file": {"type": "path", "no_log": False},  # a path, not the password
            "ansible_vault": {"type": "path", "default": "ansible-vault"},
            "mode": {"type": "raw", "default": "0600"},
            "adopt_from": {"type": "str"},
            "content": {"type": "str", "no_log": True},
            "sha256": {"type": "str"},
        },
        required_if=[("kind", "gpg_signing", ["uid", "public_path"])],
        required_by={"adopt_from": ["sha256"]},
        supports_check_mode=True,
    )
    p = module.params
    mode = int(str(p["mode"]), 8)
    paths = [p["path"]] + ([p["public_path"]] if p["kind"] == "gpg_signing" else [])
    present = [path for path in paths if os.path.lexists(path)]
    if present == paths:
        module.exit_json(changed=False, created=[], adopted=[])
    if p["adopt_from"] and p["kind"] != "random":
        module.fail_json(msg="only a random secret (one file) can be adopted")
    if present:
        module.fail_json(
            msg=f"{present[0]} exists but not the rest of its pair ({paths}); restore it or move it away"
        )
    if p["vault"] and not (p["vault_password_file"] and os.path.exists(p["vault_password_file"])):
        module.fail_json(
            msg=f"{p['path']} must be vaulted, and no vault password file is set (ansible.cfg vault_password_file)"
        )
    adopted = [f"{p['path']} from {p['adopt_from']}"] if p["adopt_from"] else []
    if module.check_mode:
        module.exit_json(changed=True, created=[] if adopted else paths, adopted=adopted)

    if adopted:
        data = base64.b64decode(p["content"] or "")
        if hashlib.sha256(data).hexdigest() != p["sha256"]:
            module.fail_json(msg=f"the copy read from {p['adopt_from']} does not match the agreed checksum")
        write_new(module, p["path"], data, mode, p["vault"])
        module.exit_json(changed=True, created=[], adopted=adopted)
    if p["kind"] == "random":
        if p["length"] < 32:
            module.fail_json(msg="length must be at least 32 bytes")
        write_new(module, p["path"], secrets.token_bytes(p["length"]), mode, p["vault"])
    else:
        secret, public = gpg_pair(module, p["uid"])
        write_new(module, p["path"], secret.encode(), mode, p["vault"])
        write_new(module, p["public_path"], public.encode(), mode, False)
    module.exit_json(changed=True, created=paths, adopted=[])


if __name__ == "__main__":
    main()
