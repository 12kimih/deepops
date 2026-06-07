export EASYBUILD_PREFIX={{ sm_prefix }}
export EASYBUILD_MODULES_TOOL=Lmod
module purge
# Clear any EBROOT* env left by previously loaded modules. Anchor the match to
# the variable NAME (^EBROOT) so a value that merely contains "EBROOT" cannot
# cause an unrelated variable (e.g. PATH) to be unset.
unset $(env | grep '^EBROOT' | awk -F'=' '{print $1}')
module load EasyBuild
