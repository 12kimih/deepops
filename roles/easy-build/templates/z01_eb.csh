setenv EASYBUILD_PREFIX {{ sm_prefix }}
setenv EASYBUILD_MODULES_TOOL Lmod
module purge
# Clear any EBROOT* env left by previously loaded modules. csh has no `unset
# $(...)`: use foreach + unsetenv (unset only removes csh shell vars, not env
# vars). Anchor on the variable NAME (^EBROOT) so a value containing "EBROOT"
# cannot unset an unrelated variable such as PATH.
foreach v ( `env | grep '^EBROOT' | awk -F'=' '{print $1}'` )
    unsetenv $v
end
module load EasyBuild
