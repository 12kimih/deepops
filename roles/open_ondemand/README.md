# open_ondemand

Installs the [Open OnDemand](https://openondemand.org/) web portal for a Slurm
cluster from the official OSC apt repository, following the
[installation](https://osc.github.io/ood-documentation/latest/installation/install-software.html)
and release-note upgrade steps. Debian family, Ubuntu 24.04 and later.

What it sets up:

- **Pinned packages.** `ondemand`, `ondemand-dex` and `nodejs` (from NodeSource: the
  Node.js major OnDemand depends on, which Ubuntu does not ship) at the versions in
  `open_ondemand_version`, `open_ondemand_dex_version` and `open_ondemand_nodejs_version`.
  Each repository key is fetched with a fixed checksum and its fingerprint is
  checked before apt trusts it.
- **One port, TLS only.** Apache listens only on `open_ondemand_port`, with the
  certificate from the inventory. No port-80 redirect, and the stock sites are
  disabled.
- **Login through Dex.** With the cluster's own LDAP (`ldap_suffix`), Dex connects
  to it by itself: StartTLS against the cluster CA, bound as the `dex` reader whose
  password `roles/site_secrets` makes (`roles/directory_defaults`). At least one
  connector is required; without one Dex would serve a static `ood@localhost` user,
  so the role refuses to run.
- **Core apps only.** The node proxies (`node_uri`, `rnode_uri`) stay off,
  `host_regex` is never allow-any, and the file browser is limited to
  `open_ondemand_allowlist_paths` (`OOD_ALLOWLIST_PATH`).
- **Slurm cluster file** named after `slurm_cluster_name`, since OOD 4.1+ passes
  the file name to Slurm.
- **Proxied services** (`open_ondemand_proxied_services`): a local web service
  such as Grafana published under a path, behind the portal login, with the
  user passed in a header that clients cannot set themselves.
- **Checks every run.** `update_ood_portal --detailed-exitcodes` must exit 0 or
  3 (exit 4 means `ood-portal.conf` was edited by hand). `dpkg -V` must show no
  difference in the OOD packages except the conffiles the role writes.
- **Rebuilds a broken install.** When package files are missing or the install
  was built for another release, it archives `/etc/ood` and `/var/www/ood` to
  `open_ondemand_backup_dir`, purges the packages and reinstalls them.
- **Certificate expiry metric.** `open_ondemand_tls_cert_not_after_seconds` is
  published through node_exporter's textfile directory, daily and on each run.

**Off means removed** (`open_ondemand_state`, set by a deploy from
`slurm_install_open_ondemand`; [component state](../../docs/deepops/component-state.md)):
present on the Slurm controller when the switch is on, absent on every other host the
playbook runs on. Absent archives `/etc/ood` and the portal site to
`open_ondemand_backup_dir` (data, never removed), removes the packages, repositories,
keys, TLS files and metrics the role installed, through the component record (packages
another component still needs stay), and gives Apache back its stock `ports.conf` and
default site; apache2 itself stays. A host deployed before the record existed, or the
release-package build of older OOD, is adopted and cleaned up the same way.

The `www-data` sudo rule in `/etc/sudoers.d/ood` belongs to the `ondemand`
package. It lets Apache start each user's server through `nginx_stage` and
nothing else. OOD cannot work without it.

After a deploy, run `scripts/validation/validate_open_ondemand.py --json` on
the portal host.

## Variables

<!-- BEGIN GENERATED: role-variables:open_ondemand -->
| Variable | Default | Description |
|---|---|---|
| `open_ondemand_state` | `present` | present or absent (docs/deepops/component-state.md); a deploy sets it from deepops_components. |
| `open_ondemand_series` | `'4.2'` | Pinned versions. A bump changes the series, the repo path and these together; read that series' release notes first (config keys and dependencies change per minor). |
| `open_ondemand_version` | `4.2.5` | Their upstream is the series' APT repository they install from (noble's index; OSC publishes each release there some days after its git tag), so a pin is never ahead of what apt can install. |
| `open_ondemand_dex_version` | `2.45.1.2` |  |
| `open_ondemand_nodejs_version` | `22.23.5-1nodesource1` | OOD 4.2 depends on nodejs >= 22, << 23, which the distribution does not ship. |
| `open_ondemand_repo_url` | `https://apt.osc.edu/ondemand/{{ open_ondemand_series }}/web/apt` | Package repositories. Each key file is fetched with a fixed checksum, then its fingerprint is checked against the publisher's, so a changed key stops the run. |
| `open_ondemand_repo_key_url` | `https://apt.osc.edu/ondemand/DEB-GPG-KEY-ondemand-SHA512` |  |
| `open_ondemand_repo_key_checksum` | `sha256:91b512eee95299ebdcb50153fd3d13a367381712c7f555af1ec518171cb63e17` |  |
| `open_ondemand_repo_key_fingerprint` | `3D4CD7E7D4BC45263AC11388C2ADB19AFD775498` |  |
| `open_ondemand_nodejs_repo_url` | `https://deb.nodesource.com/node_22.x` |  |
| `open_ondemand_nodejs_repo_key_url` | `https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key` |  |
| `open_ondemand_nodejs_repo_key_checksum` | `sha256:b42e0321dabdc24e892115da705cf061167eac12a317f23d329862d0aa0a271d` |  |
| `open_ondemand_nodejs_repo_key_fingerprint` | `6F71F525282841EEDAF851B42F59B5F99B1BE0B4` |  |
| `open_ondemand_tls_cert_src` | `''` | TLS certificate chain and key, as files under the inventory (the key vaulted). Required: the portal never serves without TLS. |
| `open_ondemand_tls_key_src` | `''` |  |
| `open_ondemand_tls_dir` | `/etc/ssl/open-ondemand` |  |
| `open_ondemand_dex_connectors` | `'{{ [open_ondemand_dex_ldap_connector] if ldap_suffix \| length > 0 else [] }}'` | Login: Dex (bundled with OOD) in front of the cluster's own LDAP once the site has one (ldap_suffix): open_ondemand_dex_ldap_connector, which binds as the dex reader whose password roles/site_secrets makes (roles/directory_defaults). Or one or more Dex connectors of your own, as in the OOD docs (type, id, name, config). Required: without a connector Dex falls back to a static ood@localhost user, which the role refuses. |
| `open_ondemand_dex_ldap_name` | `'{{ open_ondemand_cluster_title }}'` | The name on the login page's button for the cluster's LDAP. |
| `open_ondemand_oidc_crypto_passphrase` | `''` | mod_auth_openidc's session encryption passphrase; update_ood_portal refuses to run without one. Vault a long random value per site. |
| `open_ondemand_host_regex` | `({{ (groups['slurm-node'] \| default([])) \| map('regex_escape') \| join('\|') }})` | Hosts the portal may proxy to, as a regex. Used only by the node proxies, which stay off (no interactive apps); still never the allow-any default "[^/]+". |
| `open_ondemand_allowlist_paths` | `[/home]` | Directories the file browser and shell may open (OOD_ALLOWLIST_PATH). Paths outside them are refused. Empty = no restriction, which the role refuses. |
| `open_ondemand_cluster_name` | `'{{ slurm_cluster_name }}'` | The Slurm cluster file: named after slurm.conf's ClusterName, as OOD 4.1+ passes the file name to Slurm. |
| `open_ondemand_cluster_title` | `'{{ open_ondemand_cluster_name }}'` |  |
| `open_ondemand_login_host` | `'{{ inventory_hostname }}'` |  |
| `open_ondemand_proxied_services` | `[]` | A local web service published under a path of the portal, behind its login, e.g. Grafana: {path: /grafana/, upstream: "http://127.0.0.1:3000/grafana/", user_header: X-WEBAUTH-USER, title: Grafana, icon: "fas://chart-line"}. The upstream keeps the same path (the role refuses another: a service served from its sub path redirects a stripped request back to it, forever). The portal sets user_header to the logged-in user and drops any value a client sends; a title puts a link to it in the portal's top menu. Empty = none. |
| `open_ondemand_nav_groups` | `[apps, files, jobs, clusters]` | The portal's top menu (OOD's nav_bar; a list replaces OOD's own menu): these, OOD's names for the pinned apps and its app groups, then a link to each proxied service with a title. |
| `open_ondemand_overview` | `{}` | The cluster's overview, a portal page behind the login (OOD custom_pages and a dashboard widget): {nodes: [{name, cpus, memory_mb, gres}], partitions: [{name, nodes, max_time}]}, read from the site's slurm.conf, never typed: "{{ lookup('ansible.builtin.file', slurm_conf_template) \| slurm_conf_cluster }}" (plugins/filter/slurm_conf.py). Empty: no page. The page also names the commands the portal host's cluster_tools_commands publishes (roles/cluster_tools_defaults): set it where the portal host takes it. Its title in the top menu: |
| `open_ondemand_overview_title` | `Cluster` |  |
| `open_ondemand_backup_dir` | `/var/backups/open-ondemand` | Where the role keeps a copy of /etc/ood (and /var/www/ood when it rebuilds a broken install) before changing them. |
| `open_ondemand_data_paths` | `['{{ open_ondemand_backup_dir }}']` | Data the component never removes, present or absent. |
| `open_ondemand_tls_metrics` | `true` | The certificate's expiry as a node_exporter textfile metric (open_ondemand_tls_cert_not_after_seconds), refreshed daily, so an alert can fire before it lapses. |
| `open_ondemand_managed_conffiles` | `[/etc/ood/config/ood_portal.yml, /etc/ood/config/nginx_stage.yml, /etc/ood/config/ood_portal.sha256sum, /etc/apache2/sites-available/ood-portal.conf, /etc/ood/dex/config.yaml, /etc/ood/dex/dex.db]` | Files OOD's packages own that the role writes itself; dpkg -V may report these as changed. Any other difference in an OOD package fails the run. |
<!-- END GENERATED: role-variables:open_ondemand -->
