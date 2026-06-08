# EasyBuild environment for interactive use: where eb installs and which module tool it
# uses. The build-time module setup (module purge + `module load EasyBuild`) is done by the
# easy-build-packages role's build step, NOT here -- a /etc/profile.d script must not run
# `module purge`/`module load` on every login (it would wipe a user's loaded modules).
export EASYBUILD_PREFIX={{ sm_prefix }}
export EASYBUILD_MODULES_TOOL=Lmod
