#!/bin/sh
set -eu

repository_dir="${CLOUD_UPDATE_REPOSITORY_DIR:-/workspace}"
compose_file="${CLOUD_UPDATE_COMPOSE_FILE:-/workspace/compose.yaml}"
smtp_overlay="${CLOUD_UPDATE_SMTP_OVERLAY:-}"
smtp_source="${OK_WW_SMTP_PASSWORD_SOURCE:-}"
remote_name="${CLOUD_UPDATE_REMOTE:-origin}"
branch_name="${CLOUD_UPDATE_BRANCH:-master}"
autocrlf="${CLOUD_UPDATE_AUTOCRLF:-}"
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
case "$autocrlf" in
    '') ;;
    true|false|input) git -C "$repository_dir" config core.autocrlf "$autocrlf" ;;
    *)
        echo "CLOUD_UPDATE_AUTOCRLF must be true, false, input, or empty" >&2
        exit 64
        ;;
esac
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

compose_command() {
    if [ -n "$smtp_overlay" ]; then
        set -- -f "$smtp_overlay" "$@"
    fi
    docker compose --project-name "$project_name" -f "$compose_file" "$@"
}

runner_container_id() {
    compose_command ps -q cloud-runner
}

resolve_smtp_source() {
    # Compose creates the runner on the host daemon. /workspace is only a
    # client-side path. Reuse the actual host mount, including on Docker Desktop.
    existing_runner=$(compose_command ps -a -q cloud-runner) || return 1
    if [ -n "$existing_runner" ]; then
        mounted_source=$(docker inspect --format \
            '{{range .Mounts}}{{if eq .Destination "/run/secrets/smtp_password"}}{{.Source}}{{end}}{{end}}' \
            "$existing_runner") || return 1
        if [ -n "$mounted_source" ]; then
            printf '%s\n' "$mounted_source"
            return 0
        fi
    fi

    # An initial deployment may have no runner yet. Translate a relative
    # source against the host checkout before handing it to nested Compose.
    case "$smtp_source" in
        /*) printf '%s\n' "$smtp_source" ;;
        ''|?:*|\\*) return 1 ;;
        *)
            host_checkout=$(docker inspect --format \
                '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}' \
                "$HOSTNAME") || return 1
            [ -n "$host_checkout" ] || return 1
            printf '%s/%s\n' "$host_checkout" "$smtp_source"
            ;;
    esac
}

runtime_is_busy() {
    marker="$control_dir/runtime-busy"
    if [ ! -f "$marker" ] || [ -L "$marker" ]; then
        return 1
    fi
    # Preserve the busy marker when Docker cannot confirm the runner stopped.
    container_id=$(runner_container_id) || return 0
    if [ -n "$container_id" ]; then
        running=$(docker inspect --format '{{.State.Running}}' "$container_id") || return 0
        [ "$running" = "false" ] || return 0
    fi
    rm -f "$marker"
    return 1
}

defer_if_busy() {
    runtime_is_busy || return 1
    write_control retry-check "1"
    write_status deferred "Waiting for the running task or cloud game to stop"
    return 0
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
    check_remote="$2"
    write_status checking "Checking Git repository"
    if defer_if_busy; then
        return 0
    fi
    rm -f "$control_dir/retry-check"
    if [ -n "$(git -C "$repository_dir" status --porcelain --untracked-files=no)" ]; then
        echo "Skipping update because tracked files have local changes"
        rm -f "$control_dir/needs-rebuild" "$control_dir/rebuild-revision"
        write_status blocked "Tracked files have local changes"
        return 0
    fi

    local_revision=$(git -C "$repository_dir" rev-parse HEAD)
    rebuild_revision=$(cat "$control_dir/rebuild-revision" 2>/dev/null || true)
    retry_prepared="0"
    if [ "$force_rebuild" != "1" ] && [ -f "$control_dir/needs-rebuild" ] \
        && [ "$rebuild_revision" = "$local_revision" ]; then
        retry_prepared="1"
    fi
    remote_revision="$local_revision"
    if [ "$retry_prepared" = "1" ] && [ "$check_remote" != "1" ]; then
        # The previous attempt already fetched and merged this revision. A build
        # retry can reuse it, but periodic checks must still discover remote fixes.
        :
    elif ! git -C "$repository_dir" fetch --quiet "$remote_name" "$branch_name"; then
        echo "Failed to fetch $remote_name/$branch_name; retrying later" >&2
        if [ "$retry_prepared" != "1" ]; then
            if [ -f "$control_dir/needs-rebuild" ]; then
                schedule_rebuild_retry
            fi
            write_status failed "Failed to fetch Git updates"
            return 0
        fi
        echo "Retrying the already prepared revision $local_revision"
    else
        remote_revision=$(git -C "$repository_dir" rev-parse FETCH_HEAD)
    fi

    if [ "$local_revision" != "$remote_revision" ]; then
        if ! git -C "$repository_dir" merge-base --is-ancestor HEAD FETCH_HEAD; then
            echo "Skipping non-fast-forward update from $local_revision to $remote_revision" >&2
            rm -f "$control_dir/needs-rebuild" "$control_dir/rebuild-revision"
            write_status blocked "Git history is not fast-forward"
            return 0
        fi

        write_status updating "Pulling Git updates"
        echo "Updating repository from $local_revision to $remote_revision"
        write_control needs-rebuild "1"
        if ! git -C "$repository_dir" merge --ff-only FETCH_HEAD; then
            rm -f "$control_dir/needs-rebuild" "$control_dir/rebuild-revision"
            write_status failed "Failed to update Git repository"
            return 0
        fi
    elif [ "$force_rebuild" != "1" ] && [ ! -f "$control_dir/needs-rebuild" ]; then
        # The shared checkout can advance independently of the deployed image.
        if ! container_id=$(runner_container_id); then
            write_status failed "Unable to inspect the cloud-runner container"
            return 0
        fi
        if [ -z "$container_id" ]; then
            write_status up_to_date "Repository up to date; cloud-runner is stopped"
            return 0
        fi
        if ! running_revision=$(docker inspect --format \
            '{{with index .Config.Labels "org.opencontainers.image.revision"}}{{.}}{{end}}' "$container_id"); then
            write_status failed "Unable to inspect the cloud-runner revision"
            return 0
        fi
        case "$running_revision" in
            "$local_revision")
                write_status up_to_date "Already up to date"
                return 0
                ;;
            ''|unknown|dev)
                # Older Dockerfiles may omit the revision even after a successful rebuild.
                if [ "$(cat "$control_dir/applied-runner" 2>/dev/null || true)" = "$local_revision $container_id" ]; then
                    write_status up_to_date "Already up to date"
                    return 0
                fi
                ;;
        esac
        write_control needs-rebuild "1"
    fi

    if [ -s "$repository_dir/VERSION" ]; then
        build_version=$(sed -n '1{s/[[:space:]]*$//;p;}' "$repository_dir/VERSION")
    else
        build_version=$(git -C "$repository_dir" describe --tags --always --dirty)
    fi
    build_revision=$(git -C "$repository_dir" rev-parse HEAD)
    write_control rebuild-revision "$build_revision"
    if [ -n "$smtp_overlay" ]; then
        if ! host_smtp_source=$(resolve_smtp_source); then
            schedule_rebuild_retry
            write_status failed "Unable to resolve the host SMTP password file path"
            return 0
        fi
        export OK_WW_SMTP_PASSWORD_SOURCE="$host_smtp_source"
    fi
    image_name=$(compose_command config --images cloud-runner | sed -n '1p')
    if [ -z "$image_name" ]; then
        schedule_rebuild_retry
        write_status failed "Unable to determine the cloud-runner image"
        return 0
    fi
    write_status rebuilding "Building Docker image"
    if ! DOCKER_BUILDKIT=1 CLOUD_BUILD_VERSION="$build_version" CLOUD_BUILD_REVISION="$build_revision" \
        docker build --platform linux/amd64 \
        --build-arg "OK_WW_BUILD_VERSION=$build_version" \
        --build-arg "OK_WW_BUILD_REVISION=$build_revision" \
        -f "$repository_dir/docker/Dockerfile" -t "$image_name" "$repository_dir"; then
        schedule_rebuild_retry
        write_status failed "Docker image build failed"
        return 0
    fi
    if defer_if_busy; then
        return 0
    fi
    if ! CLOUD_BUILD_VERSION="$build_version" CLOUD_BUILD_REVISION="$build_revision" \
        compose_command up -d --no-deps --force-recreate --wait \
        --wait-timeout "$health_timeout_seconds" cloud-runner; then
        schedule_rebuild_retry
        write_status failed "Docker container update failed"
        return 0
    fi
    if container_id=$(runner_container_id) && [ -n "$container_id" ]; then
        write_control applied-runner "$build_revision $container_id"
    fi
    rm -f "$control_dir/needs-rebuild" "$control_dir/next-retry" "$control_dir/rebuild-revision"
    write_status up_to_date "Docker updated to $build_version"
    echo "Cloud runner updated to $build_revision ($build_version)"
}

echo "Watching $remote_name/$branch_name every ${interval_seconds}s"
write_status idle "Waiting for update check"
next_check=0
if [ -f "$control_dir/needs-rebuild" ]; then
    next_check=$(cat "$control_dir/next-remote-check" 2>/dev/null || printf '0')
    case "$next_check" in
        ''|*[!0-9]*) next_check=0 ;;
    esac
fi
while true; do
    write_control available "$(date +%s)"
    now=$(date +%s)
    force_rebuild="0"
    check_remote="0"
    if [ "$now" -ge "$next_check" ]; then
        check_remote="1"
    fi
    if [ -f "$control_dir/request" ] && [ ! -L "$control_dir/request" ]; then
        mv "$control_dir/request" "$control_dir/request.in-progress"
    fi
    if [ -f "$control_dir/request.in-progress" ] && [ ! -L "$control_dir/request.in-progress" ]; then
        rm -f "$control_dir/rebuild-revision"
        write_control needs-rebuild "1"
        force_rebuild="1"
    fi
    if [ "$force_rebuild" = "1" ] || rebuild_retry_due \
        || [ -f "$control_dir/retry-check" ] || [ "$check_remote" = "1" ]; then
        check_for_update "$force_rebuild" "$check_remote"
        rm -f "$control_dir/request.in-progress"
        if [ ! -f "$control_dir/needs-rebuild" ]; then
            rm -f "$control_dir/next-retry"
        fi
        # Local rebuild retries must not keep postponing the next remote check.
        # A busy runtime leaves the check due until it can actually run.
        if [ ! -f "$control_dir/retry-check" ] \
            && { [ "$force_rebuild" = "1" ] || [ "$check_remote" = "1" ]; }; then
            next_check=$((now + interval_seconds))
        fi
        if [ -f "$control_dir/needs-rebuild" ]; then
            write_control next-remote-check "$next_check"
        else
            rm -f "$control_dir/next-remote-check"
        fi
    fi
    sleep "$poll_seconds"
done
