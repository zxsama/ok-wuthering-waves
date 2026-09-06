#!/bin/sh
set -eu

repository_dir="${CLOUD_UPDATE_REPOSITORY_DIR:-/workspace}"
compose_file="${CLOUD_UPDATE_COMPOSE_FILE:-/workspace/compose.yaml}"
remote_name="${CLOUD_UPDATE_REMOTE:-origin}"
branch_name="${CLOUD_UPDATE_BRANCH:-master}"
interval_seconds="${CLOUD_UPDATE_INTERVAL_SECONDS:-300}"
project_name="${CLOUD_UPDATE_PROJECT_NAME:-}"

case "$interval_seconds" in
    ''|*[!0-9]*)
        echo "CLOUD_UPDATE_INTERVAL_SECONDS must be a positive integer" >&2
        exit 64
        ;;
esac
if [ "$interval_seconds" -lt 30 ]; then
    echo "CLOUD_UPDATE_INTERVAL_SECONDS must be at least 30" >&2
    exit 64
fi
if [ ! -d "$repository_dir/.git" ]; then
    echo "Git repository not found at $repository_dir" >&2
    exit 66
fi
if [ ! -f "$compose_file" ]; then
    echo "Compose file not found at $compose_file" >&2
    exit 66
fi

git config --global --add safe.directory "$repository_dir"

if [ -z "$project_name" ]; then
    project_name=$(docker inspect \
        --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
        "$HOSTNAME" 2>/dev/null || true)
fi
if [ -z "$project_name" ]; then
    echo "Unable to determine the Compose project name" >&2
    exit 65
fi

check_for_update() {
    if [ -n "$(git -C "$repository_dir" status --porcelain --untracked-files=no)" ]; then
        echo "Skipping update because tracked files have local changes"
        return
    fi

    if ! git -C "$repository_dir" fetch --quiet "$remote_name" "$branch_name"; then
        echo "Failed to fetch $remote_name/$branch_name; retrying later" >&2
        return
    fi

    local_revision=$(git -C "$repository_dir" rev-parse HEAD)
    remote_revision=$(git -C "$repository_dir" rev-parse FETCH_HEAD)
    if [ "$local_revision" = "$remote_revision" ]; then
        return
    fi
    if ! git -C "$repository_dir" merge-base --is-ancestor HEAD FETCH_HEAD; then
        echo "Skipping non-fast-forward update from $local_revision to $remote_revision" >&2
        return
    fi

    echo "Updating repository from $local_revision to $remote_revision"
    git -C "$repository_dir" merge --ff-only FETCH_HEAD
    docker compose --project-name "$project_name" -f "$compose_file" build cloud-runner
    docker compose --project-name "$project_name" -f "$compose_file" \
        up -d --no-deps --force-recreate cloud-runner
    echo "Cloud runner updated to $remote_revision"
}

echo "Watching $remote_name/$branch_name every ${interval_seconds}s"
while true; do
    check_for_update
    sleep "$interval_seconds"
done
