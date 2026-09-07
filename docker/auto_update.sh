#!/bin/sh
set -eu

repository_dir="${CLOUD_UPDATE_REPOSITORY_DIR:-/workspace}"
compose_file="${CLOUD_UPDATE_COMPOSE_FILE:-/workspace/compose.yaml}"
remote_name="${CLOUD_UPDATE_REMOTE:-origin}"
branch_name="${CLOUD_UPDATE_BRANCH:-master}"
interval_seconds="${CLOUD_UPDATE_INTERVAL_SECONDS:-86400}"
poll_seconds="${CLOUD_UPDATE_POLL_SECONDS:-1}"
health_timeout_seconds="${CLOUD_UPDATE_HEALTH_TIMEOUT_SECONDS:-240}"
retry_seconds="${CLOUD_UPDATE_RETRY_SECONDS:-300}"
project_name="${CLOUD_UPDATE_PROJECT_NAME:-}"
control_dir="${CLOUD_UPDATE_CONTROL_DIR:-/run/ok-ww-update}"

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
case "$poll_seconds" in
    ''|*[!0-9]*|0)
        echo "CLOUD_UPDATE_POLL_SECONDS must be a positive integer" >&2
        exit 64
        ;;
esac
case "$retry_seconds" in
    ''|*[!0-9]*|0)
        echo "CLOUD_UPDATE_RETRY_SECONDS must be a positive integer" >&2
        exit 64
        ;;
esac
if [ ! -d "$repository_dir/.git" ]; then
    echo "Git repository not found at $repository_dir" >&2
    exit 66
fi
if [ ! -f "$compose_file" ]; then
    echo "Compose file not found at $compose_file" >&2
    exit 66
fi

git config --global --add safe.directory "$repository_dir"
mkdir -p "$control_dir"

write_control() {
    name="$1"
    value="$2"
    temporary=$(mktemp "$control_dir/.${name}.XXXXXX")
    chmod 0644 "$temporary"
    printf '%s\n' "$value" >"$temporary"
    mv "$temporary" "$control_dir/$name"
}

write_status() {
    write_control status "$1"
    write_control message "$2"
}

schedule_rebuild_retry() {
    write_control needs-rebuild "1"
    write_control next-retry "$(($(date +%s) + retry_seconds))"
}

rebuild_retry_due() {
    [ -f "$control_dir/needs-rebuild" ] || return 1
    next_retry=$(cat "$control_dir/next-retry" 2>/dev/null || printf '0')
    case "$next_retry" in
        ''|*[!0-9]*) next_retry=0 ;;
    esac
    [ "$(date +%s)" -ge "$next_retry" ]
}

runtime_is_busy() {
    marker="$control_dir/runtime-busy"
    if [ ! -f "$marker" ] || [ -L "$marker" ]; then
        return 1
    fi
    container_id=$(docker compose --project-name "$project_name" -f "$compose_file" \
        ps -q cloud-runner 2>/dev/null || true)
    if [ -n "$container_id" ] && [ "$(docker inspect --format '{{.State.Running}}' \
        "$container_id" 2>/dev/null || true)" = "true" ]; then
        return 0
    fi
    rm -f "$marker"
    return 1
}

cleanup_control() {
    rm -f "$control_dir/available"
    if [ -n "${heartbeat_pid:-}" ]; then
        kill "$heartbeat_pid" 2>/dev/null || true
    fi
}
trap cleanup_control EXIT
trap 'exit 0' INT TERM
write_control available "$(date +%s)"
(
    while true; do
        write_control available "$(date +%s)"
        sleep "$poll_seconds"
    done
) &
heartbeat_pid=$!

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
    force_rebuild="$1"
    write_status checking "Checking Git repository"
    if runtime_is_busy; then
        write_control retry-check "1"
        write_status deferred "Waiting for the running task or cloud game to stop"
        return 0
    fi
    rm -f "$control_dir/retry-check"
    if [ -n "$(git -C "$repository_dir" status --porcelain --untracked-files=no)" ]; then
        echo "Skipping update because tracked files have local changes"
        rm -f "$control_dir/needs-rebuild"
        write_status blocked "Tracked files have local changes"
        return 0
    fi

    if ! git -C "$repository_dir" fetch --quiet "$remote_name" "$branch_name"; then
        echo "Failed to fetch $remote_name/$branch_name; retrying later" >&2
        if [ -f "$control_dir/needs-rebuild" ]; then
            schedule_rebuild_retry
        fi
        write_status failed "Failed to fetch Git updates"
        return 0
    fi

    local_revision=$(git -C "$repository_dir" rev-parse HEAD)
    remote_revision=$(git -C "$repository_dir" rev-parse FETCH_HEAD)
    if [ "$local_revision" != "$remote_revision" ]; then
        if ! git -C "$repository_dir" merge-base --is-ancestor HEAD FETCH_HEAD; then
            echo "Skipping non-fast-forward update from $local_revision to $remote_revision" >&2
            rm -f "$control_dir/needs-rebuild"
            write_status blocked "Git history is not fast-forward"
            return 0
        fi

        write_status updating "Pulling Git updates"
        echo "Updating repository from $local_revision to $remote_revision"
        if ! git -C "$repository_dir" merge --ff-only FETCH_HEAD; then
            rm -f "$control_dir/needs-rebuild"
            write_status failed "Failed to update Git repository"
            return 0
        fi
        write_control needs-rebuild "1"
    elif [ "$force_rebuild" != "1" ] && [ ! -f "$control_dir/needs-rebuild" ]; then
        write_status up_to_date "Already up to date"
        return 0
    fi

    if [ -s "$repository_dir/VERSION" ]; then
        build_version=$(sed -n '1{s/[[:space:]]*$//;p;}' "$repository_dir/VERSION")
    else
        build_version=$(git -C "$repository_dir" describe --tags --always --dirty)
    fi
    build_revision=$(git -C "$repository_dir" rev-parse HEAD)
    image_name=$(docker compose --project-name "$project_name" -f "$compose_file" \
        config --images cloud-runner | sed -n '1p')
    if [ -z "$image_name" ]; then
        schedule_rebuild_retry
        write_status failed "Unable to determine the cloud-runner image"
        return 0
    fi
    write_status rebuilding "Building Docker image"
    if ! CLOUD_BUILD_VERSION="$build_version" CLOUD_BUILD_REVISION="$build_revision" \
        docker build --platform linux/amd64 \
        --build-arg "OK_WW_BUILD_VERSION=$build_version" \
        --build-arg "OK_WW_BUILD_REVISION=$build_revision" \
        -f "$repository_dir/docker/Dockerfile" -t "$image_name" "$repository_dir"; then
        schedule_rebuild_retry
        write_status failed "Docker image build failed"
        return 0
    fi
    if ! CLOUD_BUILD_VERSION="$build_version" CLOUD_BUILD_REVISION="$build_revision" \
        docker compose --project-name "$project_name" -f "$compose_file" \
        up -d --no-deps --force-recreate --wait \
        --wait-timeout "$health_timeout_seconds" cloud-runner; then
        schedule_rebuild_retry
        write_status failed "Docker container update failed"
        return 0
    fi
    rm -f "$control_dir/needs-rebuild" "$control_dir/next-retry"
    write_status up_to_date "Docker updated to $build_version"
    echo "Cloud runner updated to $build_revision ($build_version)"
}

echo "Watching $remote_name/$branch_name every ${interval_seconds}s"
write_status idle "Waiting for update check"
next_check=0
while true; do
    write_control available "$(date +%s)"
    now=$(date +%s)
    force_rebuild="0"
    if [ -f "$control_dir/request" ] && [ ! -L "$control_dir/request" ]; then
        if mv "$control_dir/request" "$control_dir/request.in-progress"; then
            write_control needs-rebuild "1"
        fi
    fi
    if [ -f "$control_dir/request.in-progress" ] && [ ! -L "$control_dir/request.in-progress" ]; then
        force_rebuild="1"
    fi
    if [ "$force_rebuild" = "1" ] || rebuild_retry_due \
        || [ -f "$control_dir/retry-check" ] || [ "$now" -ge "$next_check" ]; then
        check_for_update "$force_rebuild"
        rm -f "$control_dir/request.in-progress"
        if [ ! -f "$control_dir/needs-rebuild" ]; then
            rm -f "$control_dir/next-retry"
        fi
        next_check=$((now + interval_seconds))
    fi
    sleep "$poll_seconds"
done
