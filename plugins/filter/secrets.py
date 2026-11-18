"""required_secret: refuse an empty or placeholder secret at the point it is used.

    {{ slurm_db_password | required_secret('slurm_db_password') }}

Role defaults and config.example ship placeholders (ReplaceWithASecurePasswordInTheVault,
deepops, changeme, ...) so a site can see what to set. Rendering one into a live file
means a known password, or a MUNGE key anyone can recompute, so the run stops instead
and says which variable to vault (ansible-vault encrypt_string).
"""

import re

PLACEHOLDER = re.compile(r"^(|.*replace.*vault.*|deepops|changeme|change-me|password|secret|admin)$", re.I)


def is_placeholder(value):
    return value is None or bool(PLACEHOLDER.match(str(value).strip()))


def required_secret(value, name):
    if is_placeholder(value):
        from ansible.errors import AnsibleFilterError

        raise AnsibleFilterError(
            f"{name} is empty or a placeholder. Set a real value in config/group_vars, "
            f"encrypted: ansible-vault encrypt_string --name {name}"
        )
    return value


class FilterModule:
    def filters(self):
        return {"required_secret": required_secret}
