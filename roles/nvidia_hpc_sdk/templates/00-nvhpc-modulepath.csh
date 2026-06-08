#!/usr/bin/env csh
# Register the NVIDIA HPC SDK modulefiles tree with Lmod via LMOD_SITE_MODULEPATH
# (defined before lmod.{sh,csh} per Lmod docs; prepended to $MODULEPATH and preserved
# across `module reset`). https://lmod.readthedocs.io/en/latest/090_configuring_lmod.html
if ( ! $?LMOD_SITE_MODULEPATH ) then
    setenv LMOD_SITE_MODULEPATH "{{ hpcsdk_install_dir }}/modulefiles"
else
    setenv LMOD_SITE_MODULEPATH "{{ hpcsdk_install_dir }}/modulefiles:${LMOD_SITE_MODULEPATH}"
endif
