#!/usr/bin/env bash
# Exercise 10: a compressing archive_command.
#   archive_command = '/lab/scripts/archive-wal-gzip.sh %p %f'
#   restore_command = 'gunzip -c /archive/wal-gz/%f.gz > %p'
# Same three rules as archive-wal.sh (durable, never overwrite, idempotent);
# the idempotency check compares the *uncompressed* contents.
set -euo pipefail

src="$1"
name="$2"
dest_dir="/archive/wal-gz"
dest="$dest_dir/$name.gz"

mkdir -p "$dest_dir"
if [[ -f "$dest" ]]; then
    if gunzip -c "$dest" | cmp -s - "$src"; then
        exit 0
    fi
    echo "archive-wal-gzip: $dest exists with different contents, refusing to overwrite" >&2
    exit 1
fi

gzip -c "$src" > "$dest.tmp"
sync "$dest.tmp"
mv "$dest.tmp" "$dest"
sync "$dest_dir"
