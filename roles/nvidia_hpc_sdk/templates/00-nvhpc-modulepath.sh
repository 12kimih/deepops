#!/usr/bin/env bash
# Register the NVIDIA HPC SDK modulefiles tree with Lmod via LMOD_SITE_MODULEPATH.
# Lmod's docs say to define the modulepath BEFORE lmod.{sh,csh} runs (via
# LMOD_SITE_MODULEPATH / $MODULEPATH / .modulepath) and NOT to edit $MODULEPATH in a
# later /etc/profile.d file -- so this sorts before lmod.sh and uses the site hook,
# which Lmod prepends to $MODULEPATH and preserves across `module reset`.
# https://lmod.readthedocs.io/en/latest/090_configuring_lmod.html
case ":${LMOD_SITE_MODULEPATH:-}:" in
  *":{{ hpcsdk_install_dir }}/modulefiles:"*) : ;;
  *) export LMOD_SITE_MODULEPATH="{{ hpcsdk_install_dir }}/modulefiles${LMOD_SITE_MODULEPATH:+:${LMOD_SITE_MODULEPATH}}" ;;
esac
