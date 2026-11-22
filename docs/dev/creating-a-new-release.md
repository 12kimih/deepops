# Creating a new DeepOps release

- [Update software versions and dependencies for new release](#update-software-versions-and-dependencies-for-new-release)
- [Create PR with release notes](#create-pr-with-release-notes)
- [Create release](#create-release)



As of release 22.04, DeepOps no longer utilizes release branches.
All changes should be merged with the main branch and a release tag created.

## Update software versions and dependencies for new release

Every pinned version lives once, in the role default (or `pyproject.toml`,
`roles/requirements.yml`, the Kubespray submodule) that uses it, with an `# upstream:`
line naming where its releases come from. Nothing lists them by hand: compare every pin
with its upstream, and see them all in one table, with

```bash
uv run python scripts/deepops/upstream.py      # behind / current, file:line of each pin
less docs/deepops/versions.md                  # generated: scripts/docs/generate.py
```

For each pin to move: read that release's notes and install guide, change the one
default (with its checksum or digest, which the download and image roles require),
regenerate the docs (`uv run python scripts/docs/generate.py`), and run
`scripts/check.sh`. Two pins have their own tools: `scripts/deepops/cuda_releases.py`
regenerates the CUDA catalog, and `scripts/deepops/image_pins.py pin <repo:tag>` prints a
container image's digest pin. Kubespray moves with its submodule
(`git -C submodules/kubespray checkout <tag>`, then its upgrade notes and
`./scripts/setup.sh` for its Ansible).

## Create PR with release notes

Once all pull requests for the release have been merged, including software version updates,
and all QA tests are passing, create a new PR with release notes and an updated release tag
in the main `README.md` file. See [#1164](https://github.com/NVIDIA/deepops/pull/1164) as an example.

## Create release

Once the previous pull request with release notes and tag update has been merged, create a new release
with a new tag, pointing at the main branch and using the release notes from the PR.
