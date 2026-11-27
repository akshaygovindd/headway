#!/usr/bin/env bash
# Point the image URIs in a terraform.tfvars at one tag, then check the result.
# Usage: scripts/set_image_tags.sh <tfvars> <tag> <enable_r3: true|false> <enable_r4: true|false>
# curate and transform always; train when R3 is on; api when R4 is on. Fails, naming the images,
# when any image that is in use did not end up on the tag (a missing line, or another repository).
set -euo pipefail
file=$1 tag=$2 r3=${3:-false} r4=${4:-false}
test -f "$file" || { echo "$file not found" >&2; exit 1; }

for name in curate transform train api; do
  sed -i.bak -E "s|^(${name}_image_uri[[:space:]]*=[[:space:]]*\"[^\"]*headway-${name}:)[^\"]*\"|\1${tag}\"|" "$file"
done
rm -f "$file.bak"

wanted=(curate transform)
[ "$r3" = true ] && wanted+=(train)
[ "$r4" = true ] && wanted+=(api)
missing=()
for name in "${wanted[@]}"; do
  grep -Eq "^[[:space:]]*${name}_image_uri[[:space:]]*=[[:space:]]*\"[^\"]*headway-${name}:${tag}\"" "$file" || missing+=("$name")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "could not set the image tag to ${tag} in ${file} for: ${missing[*]}" >&2
  exit 1
fi
