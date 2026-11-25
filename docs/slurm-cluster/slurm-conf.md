# Slurm's config files from settings

`roles/slurm` renders `slurm.conf`, `cgroup.conf`, `gres.conf` and `slurmdbd.conf` on the
controller (the nodes fetch them, configless) from settings: mappings of Slurm's own
keys, as the man pages spell them.

| File | deepops' defaults | The site's own | Also |
|---|---|---|---|
| `slurm.conf` | `slurm_conf_defaults` | `slurm_conf` | nodes, partitions (below) |
| `cgroup.conf` | `slurm_cgroup_conf_defaults` | `slurm_cgroup_conf` | |
| `gres.conf` | `slurm_gres_conf_defaults` | `slurm_gres_conf` | each node's `slurm_gres` lines |
| `slurmdbd.conf` | `slurm_dbd_conf_defaults` | `slurm_dbd_conf` | |

The site's mapping is laid over deepops' defaults (`roles/slurm/defaults/main.yml`): a key
it sets wins, and a key set to `null` is left out, so Slurm's own default applies. A
config states only what differs from the defaults.

```yaml
# config/group_vars/slurm-cluster.yml
slurm_conf:
  PriorityType: priority/multifactor
  PriorityWeightFairshare: 1000
  SlurmctldParameters: [enable_configless, rl_enable]
  PropagateResourceLimitsExcept: null     # Slurm's default: propagate all
slurm_cgroup_conf:
  ConstrainSwapSpace: true
```

A list is written with commas (`SelectTypeParameters`), or as a line per item for a key
Slurm takes more than once (`SlurmctldHost`); `true` and `false` become `YES` and `NO`; a
value with blanks is quoted. The lines come in the man page's (alphabetical) order.

## Checked against Slurm

Every key must be one that Slurm reads in that file and part, for the Slurm the role
installs (`slurm_version`): `plugins/filter/slurm_parameters.json` holds Slurm's own
parameter tables, pinned from its source by `scripts/deepops/slurm_parameters.py`
(rerun it when `slurm_version` changes). A misspelt key (`KilWait`), one of another part
(`Weight` outside a node) or one Slurm retired (`AccountingStorageUser`,
`CgroupAutomount`) fails the rendering, before slurmctld reads it.

## What the role works out

Some values follow from the inventory and the role's other settings, and only the role
sets them (`_slurm_conf_computed` and the like in `roles/slurm/vars/main.yml`): the
cluster's name and controllers, the ports (`slurm_slurmctld_port`, `slurm_slurmd_port`,
`slurm_slurmdbd_port`), the log and state paths, the prolog, epilog and task prolog, the
per-job private directories (`NamespaceType`, `TmpFS`), the health check
(`slurm_health_check_program`, every `slurm_health_check_interval` seconds), job notices
(`MailProg`), the job_submit plugin, the GPU accounting (`GresTypes`,
`AccountingStorageTRES` with each GPU type of the nodes' gres.conf lines), and
`slurmdbd.conf`'s host, port, account and password. Setting one of them in `slurm_conf`
is refused: change the setting it comes from.

`SlurmctldParameters` must keep `enable_configless`: the nodes have no config of their own.

## Nodes

Each node's `NodeName=` line is what the role measures there, as `slurmd -C` reports it
(`CPUs`, `Boards`, `SocketsPerBoard`, `CoresPerSocket`, `ThreadsPerCore`, `RealMemory`
in whole GiB, `Gres`, and `TmpDisk` when it has a job filesystem), then
`slurm_node_policy`, then the host's `slurm_node_conf`, each read for that node:

```yaml
# config/group_vars/slurm-cluster.yml: every node
slurm_node_policy:
  NodeAddr: "{{ ansible_host }}"
  CoreSpecCount: 4
  MemSpecLimit: 8192
# config/host_vars/gpu01
slurm_node_conf: {RestrictedCoresPerGPU: 10, Weight: 30}
slurm_gres:
  - {Name: gpu, Type: gtype, File: "/dev/nvidia[0-3]", Cores: 0-47}
  - {Name: gpu, Type: gtype, File: "/dev/nvidia[4-7]", Cores: 48-95}
```

A node's `slurm_gres` are its `gres.conf` lines (without `NodeName`), and its `Gres` then
counts them by type (`gpu:gtype:8`). Without any, `Gres` is the GPUs the node has and
slurmd finds them itself (`AutoDetect=nvml`); a site that lists every GPU sets
`slurm_gres_conf: {AutoDetect: null}`. A MIG layout is a host's
`slurm_node_conf: {Gres: "gpu:3g.40gb:8,gpu:1g.10gb:16"}` ([MIG](nvml.md)).

## Partitions

`slurm_partitions` is the list of partitions, each with its `PartitionName`, every one
laid over `slurm_partition_defaults`. The default is one partition, `batch`, of every node
(`slurm_oversubscribe`, `slurm_max_job_timelimit`, `slurm_default_job_timelimit`).

```yaml
slurm_partition_defaults: {State: UP, OverSubscribe: "NO", MaxTime: 7-00:00:00}
slurm_partitions:
  - {PartitionName: gpu, Nodes: "gpu[01-04]", Default: true, DefCpuPerGPU: 8}
```

## A site's own files

`slurm_conf_template` (and `slurm_cgroup_conf_template`, `slurm_gres_conf_template`,
`slurm_dbd_conf_template`) may still point at a site's own complete template; the
settings above then do not apply to that file. To move from one to the settings, state
them and compare:

```bash
ansible-playbook playbooks/slurm-cluster/slurm-conf-compare.yml
```

It renders both on the controller, from the facts the last deploy cached, and lists
every setting they disagree on (every global key, and every node, partition and
gres.conf line field by field), the site's file first; it fails on any. The database
password is compared as a placeholder. When it lists nothing, drop the `*_template` line
and the file.
