# An apt or dpkg state file without the paragraphs of the packages in `drop` (awk -v
# drop="a b:i386 ..."): "name" for the native architecture or all, "name:arch" for that
# one. dpkg's status without them is the host as a removal leaves it
# (plugins/action/repo_package.py); apt's extended_states without them is the host with
# them marked installed by name (roles/host_baseline tasks/absent.yml). One program for
# both, read by each.
BEGIN { RS = ""; ORS = "\n\n"; "dpkg --print-architecture" | getline native
        n = split(drop, d, " "); for (i = 1; i <= n; i++) D[d[i]] = 1 }
{ p = ""; a = ""; m = split($0, L, "\n")
  for (i = 1; i <= m; i++) {
    if (L[i] ~ /^Package: /) p = substr(L[i], 10)
    if (L[i] ~ /^Architecture: /) a = substr(L[i], 15)
  }
  if ((p ":" a) in D || ((a == native || a == "all") && p in D)) next
  print }
