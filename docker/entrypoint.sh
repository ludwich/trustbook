#!/bin/sh
# Trustbook container entrypoint (DECISIONS D-005).
#
# Runs as root only long enough to read the owner of the data folder. If that owner is a non-root
# uid (typical for a Linux bind mount of ~/Trustbook), the command runs as that uid/gid so files on
# the host stay owned by the user. Otherwise (Windows/macOS Docker Desktop mounts, named volumes,
# rootless Podman, or no data folder at all, e.g. the searxng-init step) it keeps running as root.
set -eu

data_dir="${TRUSTBOOK_DATA:-/data}"

# Already started as a non-root user (e.g. `--user`): nothing to drop.
if [ "$(id -u)" -ne 0 ]; then
    exec "$@"
fi

if [ -d "$data_dir" ]; then
    owner_uid="$(stat -c %u "$data_dir")"
    owner_gid="$(stat -c %g "$data_dir")"
    if [ "$owner_uid" -ne 0 ]; then
        # The uid usually has no passwd entry in the image, so give it a writable HOME.
        export HOME=/tmp
        exec setpriv --reuid="$owner_uid" --regid="$owner_gid" --clear-groups -- "$@"
    fi
fi

exec "$@"
