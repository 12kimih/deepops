#!/usr/bin/env csh
# Register the rootless-docker modulefiles tree with Lmod via LMOD_SITE_MODULEPATH
# (lmod.sh reads it before Lmod init, prepends it to MODULEPATH, and preserves it
# across `module reset`). https://lmod.readthedocs.io/en/latest/090_configuring_lmod.html
if ( ! $?LMOD_SITE_MODULEPATH ) then
    setenv LMOD_SITE_MODULEPATH "{{ rootlessdocker_install_dir }}/modulefiles"
else
    setenv LMOD_SITE_MODULEPATH "{{ rootlessdocker_install_dir }}/modulefiles:${LMOD_SITE_MODULEPATH}"
endif
