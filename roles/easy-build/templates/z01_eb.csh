# EasyBuild environment for interactive use (where eb installs, which module tool). The
# build-time module setup is done by the easy-build-packages role, NOT here -- a profile.d
# script must not run `module purge`/`module load` on every login.
setenv EASYBUILD_PREFIX {{ sm_prefix }}
setenv EASYBUILD_MODULES_TOOL Lmod
