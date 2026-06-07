#!/usr/bin/env bash
# Register the rootless-docker modulefiles tree with Lmod via LMOD_SITE_MODULEPATH.
# Lmod's docs say to define the modulepath BEFORE z00_lmod.* runs and NOT to edit
# $MODULEPATH in a later /etc/profile.d file -- so this sorts before z00_lmod and
# uses the site hook, which z00_lmod prepends to $MODULEPATH and preserves across
# `module reset`. https://lmod.readthedocs.io/en/latest/090_configuring_lmod.html
case ":${LMOD_SITE_MODULEPATH:-}:" in
  *":{{ rootlessdocker_install_dir }}/modulefiles:"*) : ;;
  *) export LMOD_SITE_MODULEPATH="{{ rootlessdocker_install_dir }}/modulefiles${LMOD_SITE_MODULEPATH:+:${LMOD_SITE_MODULEPATH}}" ;;
esac
