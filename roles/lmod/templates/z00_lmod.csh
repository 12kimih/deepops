#!/bin/csh
# -*- shell-script -*-
########################################################################
#  This is the system wide source file for setting up
#  modules:
#
########################################################################

set MY_NAME="/usr/share/lmod/lmod/init/cshrc"



if ( ! $?MODULEPATH_ROOT ) then
    if ( $?USER) then
        setenv USER $LOGNAME
    endif

    set UNAME = `uname`
    setenv LMOD_sys    $UNAME

    setenv LMOD_arch   `uname -m`
    if ( "x$UNAME" == xAIX ) then
        setenv LMOD_arch   rs6k
    endif

    setenv TARG_TITLE_BAR_PAREN " "
    setenv LMOD_FULL_SETTARG_SUPPORT no
    setenv LMOD_SETTARG_CMD     :
    setenv LMOD_COLORIZE        yes
    setenv LMOD_PREPEND_BLOCK   normal
    setenv MODULEPATH_ROOT      "{{ sm_module_root }}"
    # setenv MODULEPATH           `/usr/share/lmod/lmod/libexec/addto --append MODULEPATH $MODULEPATH_ROOT/$LMOD_sys $MODULEPATH_ROOT/Core`
    # setenv MODULEPATH           `/usr/share/lmod/lmod/libexec/addto --append MODULEPATH /usr/share/lmod/lmod/modulefiles/Core`
    setenv MODULEPATH           "{{ sm_module_path }}"
    setenv MODULESHOME          "/usr/share/lmod/lmod"
    setenv BASH_ENV             "$MODULESHOME/init/bash"

    #
    # If MANPATH is empty, Lmod is adding a trailing ":" so that
    # the system MANPATH will be found
    if (! $?MANPATH ) then
      setenv MANPATH :
    endif
    setenv MANPATH `/usr/share/lmod/lmod/libexec/addto MANPATH /usr/share/lmod/lmod/share/man`

endif

# Prepend any site module trees registered in LMOD_SITE_MODULEPATH (set by the
# /etc/profile.d/00-*.csh snippets that sort before this one, e.g. cuda/nvhpc).
# Mirrors the LMOD_SITE_MODULEPATH loop in stock Lmod's profile.in; without it
# init/csh would silently ignore those snippets. See z00_lmod.sh for details.
# https://github.com/TACC/Lmod/blob/main/init/profile.in
if ( $?LMOD_SITE_MODULEPATH ) then
    foreach dir (`echo "$LMOD_SITE_MODULEPATH" | tr ':' ' '`)
        setenv MODULEPATH `/usr/share/lmod/lmod/libexec/addto MODULEPATH "$dir"`
    end
endif

if ( -f  /usr/share/lmod/lmod/init/csh  ) then
  source /usr/share/lmod/lmod/init/csh
endif
