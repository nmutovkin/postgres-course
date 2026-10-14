#!/usr/bin/env bash
# archive_command for the Module 18 lab:
#   archive_command = '/lab/scripts/archive-wal.sh %p %f'
#
# PostgreSQL calls this once per finished WAL segment (and for .history and
# .backup files). %p = path of the file relative to the data directory,
# %f = bare file name. Exit status 0 means "safely archived, you may recycle
# the segment"; anything else makes the archiver retry the same file later.
#
# Rules a correct archive_command must follow:
#   1. Never report success before the copy is durable (fsync'd).
#   2. Never overwrite an existing archive file with different contents.
#   3. Be idempotent: if a crash happened after copying but before PostgreSQL
#      recorded success, the same file is offered again and must succeed.
set -euo pipefail

src="$1"
name="$2"
dest_dir="/archive/wal"
dest="$dest_dir/$name"

if [[ -f "$dest" ]]; then
    if cmp -s "$src" "$dest"; then
        exit 0                      # rule 3: already archived, identical
    fi
    echo "archive-wal: $dest exists with different contents, refusing to overwrite" >&2
    exit 1                          # rule 2
fi

# Copy under a temporary name, flush it to disk, then rename atomically.
# A half-written file never appears under the final name.
cp "$src" "$dest.tmp"
sync "$dest.tmp"
mv "$dest.tmp" "$dest"
sync "$dest_dir"                    # make the rename itself durable
