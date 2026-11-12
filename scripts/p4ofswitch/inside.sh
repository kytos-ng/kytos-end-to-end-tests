#!/bin/bash
# Runs inside the kytos container; p4e2e copies it in and passes config via env.

set -eo pipefail
export LC_ALL=C

STATE=/var/lib/e2e-p4
RESULTS=/e2e-results
TMUX_SESSION=e2e
CORE_PKGS="kytos python-openflow kytos-utils"

: "${E2E_REPO:=https://github.com/kytos-ng/kytos-end-to-end-tests}"
: "${E2E_BRANCH:=master}"
: "${E2E_DIR:=/kytos-end-to-end-tests}"
: "${P4OFSWITCH_IMAGE:=amlight/p4ofswitch:latest}"
: "${P4OFSWITCH_ARCH:=tf1}"
: "${HUGEPAGES:=2048}"
: "${RERUNS:=2}"

log() { echo "==> $*" >&2; }
warn() { echo "WARNING: $*" >&2; }
die() { echo "ERROR: $*" >&2; exit 1; }

setup_hugepages() {
    local cur
    cur=$(cat /proc/sys/vm/nr_hugepages)
    if (( cur < HUGEPAGES )); then
        log "vm.nr_hugepages=$cur, raising to $HUGEPAGES (this is a host kernel setting)"
        sysctl -qw vm.nr_hugepages="$HUGEPAGES" || warn "could not set vm.nr_hugepages; set it on the host"
    fi
    cur=$(awk '/^HugePages_Total/ {print $2}' /proc/meminfo)
    if (( cur < HUGEPAGES )); then
        warn "only $cur huge pages allocated (wanted $HUGEPAGES): the host may lack free memory"
    fi
    if ! mountpoint -q /dev/hugepages; then
        mkdir -p /dev/hugepages
        mount -t hugetlbfs none /dev/hugepages || warn "could not mount hugetlbfs at /dev/hugepages"
    fi
}

install_mininet() {
    [ -f "$STATE/mininet.done" ] && return
    log "installing mininet with docker hosts support"
    curl -fsSL -o /tmp/mininet.deb "$MININET_DEB_URL"
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends --no-install-suggests gnupg /tmp/mininet.deb
    rm -f /tmp/mininet.deb
    touch "$STATE/mininet.done"
}

install_docker() {
    command -v dockerd >/dev/null && return
    log "installing docker-ce"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends --no-install-suggests \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

start_dockerd() {
    if ! docker info >/dev/null 2>&1; then
        log "starting dockerd"
        setsid dockerd >/var/log/dockerd.log 2>&1 </dev/null &

        for _ in $(seq 1 60); do
            docker info >/dev/null 2>&1 && break
            sleep 1
        done
        docker info >/dev/null 2>&1 || die "dockerd did not start, see /var/log/dockerd.log"
    fi
    # /var/lib/docker may be a kept volume; drop leftover switch containers
    local stale
    stale=$(docker ps -aq)
    if [ -n "$stale" ]; then
        log "removing $(echo "$stale" | wc -l) leftover switch container(s)"
        echo "$stale" | xargs docker rm -f >/dev/null
    fi
}

record_baseline() {
    [ -f "$STATE/baseline-pip-check.txt" ] && return
    log "recording the image baseline (NApp commits, pip check)"
    mkdir -p "$STATE/orig"
    local d
    for d in /src/*/; do
        git -C "$d" rev-parse HEAD > "$STATE/orig/$(basename "$d")"
    done
    python3 -m pip check > "$STATE/baseline-pip-check.txt" 2>&1 || true
}

cmd_provision() {
    if run_active; then
        die "a test run is in progress; wait for it or run p4e2e stop"
    fi
    mkdir -p "$STATE" "$RESULTS"
    pgrep -x rsyslogd >/dev/null || service rsyslog start >/dev/null 2>&1 || warn "rsyslog did not start"
    setup_hugepages
    install_mininet
    install_docker
    start_dockerd
    log "pulling $P4OFSWITCH_IMAGE"
    docker pull -q "$P4OFSWITCH_IMAGE" >/dev/null
    if [ ! -d "$E2E_DIR/.git" ]; then
        log "cloning $E2E_REPO ($E2E_BRANCH) into $E2E_DIR"
        git clone -q --branch "$E2E_BRANCH" "$E2E_REPO" "$E2E_DIR"
        mkdir -p "$STATE/overrides"
        echo "$E2E_REPO $E2E_BRANCH" > "$STATE/overrides/e2e"
    fi
    record_baseline
    log "provisioned"
}

BUILD_HINT="build an image with this branch instead: p4e2e build-image -t docker.io/<user>/kytos-e2e:<name> --push ..."

# prints requirements the container does not satisfy, without installing
unmet_requirements() {
    local skip=$1
    shift
    python3 -m pip install --dry-run --quiet --report - "$@" 2>/dev/null | python3 -c '
import json, sys
from importlib.metadata import version, PackageNotFoundError
skip = sys.argv[1].lower().replace("_", "-")
for item in json.load(sys.stdin).get("install", []):
    meta = item["metadata"]
    name = meta["name"]
    if name.lower().replace("_", "-") == skip:
        continue
    try:
        have = version(name)
    except PackageNotFoundError:
        have = "not installed"
    wanted = meta["version"]
    print(f"{name}=={wanted} (installed: {have})")
' "$skip"
}

pip_check_vs_baseline() {
    local now new
    now=$(python3 -m pip check 2>&1 || true)
    new=$(comm -13 <(sort "$STATE/baseline-pip-check.txt") <(echo "$now" | sort) | grep -v '^No broken requirements' || true)
    if [ -n "$new" ]; then
        echo "$new" >&2
        die "pip check found new dependency problems (above); recreate the container (p4e2e up --recreate) and $BUILD_HINT"
    fi
}

parse_spec() {
    local spec=$1 default_repo=$2 val
    SPEC_NAME=${spec%%=*}
    val=${spec#*=}
    [ "$SPEC_NAME" != "$spec" ] && [ -n "$val" ] || die "bad spec '$spec', expected name=ref"
    if [[ $val == *"#"* ]]; then
        SPEC_REPO=${val%%#*}
        SPEC_REF=${val#*#}
        [[ $SPEC_REPO == *://* ]] || SPEC_REPO="https://github.com/$SPEC_REPO"
    else
        SPEC_REPO=$default_repo
        SPEC_REF=$val
    fi
    [ -n "$SPEC_REF" ] || die "bad spec '$spec': empty ref"
}

# kytos-init.sh seds settings.py
reset_init_edits() {
    git -C "$1" checkout -q -- settings.py 2>/dev/null || true
}

ensure_clean() {
    local dir=$1 dirty
    dirty=$(git -C "$dir" status --porcelain --untracked-files=no)
    if [ -n "$dirty" ] && [ "$FORCE" != 1 ]; then
        echo "$dirty" >&2
        die "$dir has local changes (above): your edits, or patches applied by the image build. Re-run with --force to discard them."
    fi
}

napp_dir() {
    local name=${1//_/-} matches
    matches=$(compgen -G "/src/*-$name" || true)
    # shellcheck disable=SC2012
    [ -n "$matches" ] || die "unknown NApp '$1'. Known: $(cd /src && ls | sed -E 's/^(kytos|amlight)-//; s/-/_/g' | tr '\n' ' ')"
    [ "$(echo "$matches" | wc -l)" -eq 1 ] || die "'$1' is ambiguous: $matches"
    echo "$matches"
}

branch_e2e() {
    reset_init_edits "$E2E_DIR"
    ensure_clean "$E2E_DIR"
    if [ "$SPEC_REF" = "@image" ]; then die "e2e has no image version; pass a branch"; fi
    git -C "$E2E_DIR" fetch -q "$SPEC_REPO" "$SPEC_REF"
    git -C "$E2E_DIR" checkout -q -f --detach FETCH_HEAD
    echo "$SPEC_REPO $SPEC_REF" > "$STATE/overrides/e2e"
    log "e2e -> $SPEC_REF $(git -C "$E2E_DIR" log -1 --oneline)"
}

branch_napp() {
    local name=$1 dir key target unmet
    dir=$(napp_dir "$name")
    key=$(basename "$dir")
    reset_init_edits "$dir"
    ensure_clean "$dir"

    if [ "$SPEC_REF" = "@image" ]; then
        target=$(cat "$STATE/orig/$key")
    else
        git -C "$dir" fetch -q "$SPEC_REPO" "$SPEC_REF"
        target=$(git -C "$dir" rev-parse FETCH_HEAD)
    fi

    if git -C "$dir" cat-file -e "$target:requirements/run.txt" 2>/dev/null; then
        local reqs=$STATE/reqs-$key.txt
        git -C "$dir" show "$target:requirements/run.txt" > "$reqs"
        if ! unmet=$(unmet_requirements "" -r "$reqs"); then
            die "$name: could not check requirements/run.txt at '$SPEC_REF' (pip install --dry-run -r $reqs)"
        fi
        if [ -n "$unmet" ]; then
            echo "$unmet" >&2
            die "$name: '$SPEC_REF' needs the packages above, which the image does not have; $BUILD_HINT"
        fi
    fi

    git -C "$dir" checkout -q -f --detach "$target"
    if [ "$SPEC_REF" = "@image" ]; then
        rm -f "$STATE/overrides/$key"
    else
        echo "$SPEC_REPO $SPEC_REF" > "$STATE/overrides/$key"
    fi
    log "$name -> $SPEC_REF $(git -C "$dir" log -1 --oneline)"
}

# kytos/kytos-utils setup.py pip-installs requirements/run.txt on every build,
# so only install once that file is already satisfied
branch_core() {
    local pkg=$1 src=$STATE/core-src/$1 unmet before after
    [ "$SPEC_REF" != "@image" ] || die "$pkg is not a git checkout, so it cannot go back to @image; recreate the container instead (p4e2e up --recreate)"
    [ -d "$src/.git" ] || git init -q "$src"
    git -C "$src" fetch -q --depth 1 "$SPEC_REPO" "$SPEC_REF"
    git -C "$src" checkout -q -f --detach FETCH_HEAD
    if [ -f "$src/requirements/run.txt" ]; then
        if ! unmet=$(unmet_requirements "$pkg" -r "$src/requirements/run.txt"); then
            die "$pkg: could not check requirements/run.txt at '$SPEC_REF'"
        fi
        if [ -n "$unmet" ]; then
            echo "$unmet" >&2
            die "$pkg: '$SPEC_REF' needs the packages above, which the image does not have; $BUILD_HINT"
        fi
    fi
    before=$(python3 -m pip list --format=freeze 2>/dev/null | grep -viE "^${pkg//[-_]/[-_]}==" | sort)
    python3 -m pip install -q --no-deps --force-reinstall "$src"
    after=$(python3 -m pip list --format=freeze 2>/dev/null | grep -viE "^${pkg//[-_]/[-_]}==" | sort)
    if [ "$before" != "$after" ]; then
        diff <(echo "$before") <(echo "$after") >&2 || true
        warn "installing $pkg also changed the packages above (its setup.py installs dependencies itself); recreate the container if results look off"
    fi
    pip_check_vs_baseline
    echo "$SPEC_REPO $SPEC_REF" > "$STATE/overrides/$pkg"
    log "$pkg -> $SPEC_REF $(git -C "$src" log -1 --format='%h %s') ($(python3 -m pip show "$pkg" 2>/dev/null | awk '/^Version/ {print $2}'))"
}

cmd_branch() {
    FORCE=0
    local specs=() spec
    for spec in "$@"; do
        case $spec in
            --force) FORCE=1 ;;
            -*) die "unknown flag $spec" ;;
            *) specs+=("$spec") ;;
        esac
    done
    [ ${#specs[@]} -gt 0 ] || die "nothing to do, pass name=ref (e.g. telemetry_int=fix/foo)"
    [ -f "$STATE/baseline-pip-check.txt" ] || die "not provisioned, run p4e2e up first"
    if run_active; then
        die "a test run is in progress; wait for it or run p4e2e stop"
    fi
    mkdir -p "$STATE/overrides"
    for spec in "${specs[@]}"; do
        local name=${spec%%=*}
        case $name in
            e2e)
                parse_spec "$spec" "$E2E_REPO"; branch_e2e ;;
            kytos)
                parse_spec "$spec" https://github.com/kytos-ng/kytos; branch_core kytos ;;
            python_openflow|python-openflow)
                parse_spec "$spec" https://github.com/kytos-ng/python-openflow; branch_core python-openflow ;;
            kytos_utils|kytos-utils)
                parse_spec "$spec" https://github.com/kytos-ng/kytos-utils; branch_core kytos-utils ;;
            *)
                name=${name//-/_}
                parse_spec "$spec" "https://github.com/kytos-ng/$name"; branch_napp "$name" ;;
        esac
    done
}

manifest() {
    local d key ov
    echo "kytos_image: ${KYTOS_IMAGE:-unknown}"
    echo "p4ofswitch_image: $P4OFSWITCH_IMAGE ($(docker image inspect -f '{{index .RepoDigests 0}}' "$P4OFSWITCH_IMAGE" 2>/dev/null || echo '?'))"
    echo "p4ofswitch_arch: $P4OFSWITCH_ARCH"
    if [ -d "$E2E_DIR/.git" ]; then
        echo "e2e: $(git -C "$E2E_DIR" rev-parse --short HEAD) $(cat "$STATE/overrides/e2e" 2>/dev/null)"
    fi
    for pkg in $CORE_PKGS; do
        ov=$(cat "$STATE/overrides/$pkg" 2>/dev/null || echo image)
        echo "$pkg: $(python3 -m pip show "$pkg" 2>/dev/null | awk '/^Version/ {print $2}') [$ov]"
    done
    for d in /src/*/; do
        key=$(basename "$d")
        ov=$(cat "$STATE/overrides/$key" 2>/dev/null || echo image)
        echo "$key: $(git -C "$d" rev-parse --short HEAD) [$ov]"
    done
}

run_finished() {
    [ -f "$RESULTS/latest/exit-code" ]
}

# run.sh records its pid, so this does not depend on tmux state
run_active() {
    local pid
    run_finished && return 1
    pid=$(cat "$RESULTS/latest/pid" 2>/dev/null) || return 1
    kill -0 "$pid" 2>/dev/null
}

cmd_status() {
    manifest
    if [ ! -e "$RESULTS/latest" ]; then
        echo "last run: none"
    elif run_finished; then
        echo "last run: $(basename "$(readlink -f "$RESULTS/latest")") finished, exit code $(cat "$RESULTS/latest/exit-code")"
    elif run_active; then
        echo "last run: $(basename "$(readlink -f "$RESULTS/latest")") RUNNING"
    else
        echo "last run: $(basename "$(readlink -f "$RESULTS/latest")") interrupted"
    fi
}

cmd_run() {
    [ -d "$E2E_DIR/.git" ] || die "not provisioned, run p4e2e up first"
    run_active && die "a test run is in progress (p4e2e logs -f / p4e2e attach), or stop it with p4e2e stop"
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
    local a
    for a in "$@"; do
        # kytos-init.sh expands $TESTS unquoted
        [[ $a =~ [[:space:]] ]] && die "argument '$a' contains whitespace, which kytos-init.sh cannot pass to pytest. Use test node ids (file.py::Class::test) or a single-word -k."
    done
    local tests="${*:-tests/}" ts dir
    ts=$(date +%Y%m%d-%H%M%S)
    dir=$RESULTS/$ts
    mkdir -p "$dir"
    date -u +%Y-%m-%dT%H:%M:%S > "$dir/started"
    ln -sfn "$dir" "$RESULTS/latest"
    { manifest; echo "tests: $tests"; echo "reruns: $RERUNS"; } > "$dir/manifest.txt"
    python3 -m pip list --format=freeze > "$dir/pip-freeze.txt" 2>/dev/null

    {
        echo '#!/bin/bash'
        printf 'echo $$ > %q\n' "$dir/pid"
        printf 'cd %q || exit 1\n' "$E2E_DIR"
        printf 'export SWITCH_CLASS=P4OfSwitch P4OFSWITCH_ARCH=%q P4OFSWITCH_IMAGE=%q\n' "$P4OFSWITCH_ARCH" "$P4OFSWITCH_IMAGE"
        printf 'export TESTS=%q RERUNS=%q\n' "$tests" "$RERUNS"
        [ -n "$MYLOCALIP" ] && printf 'export MYLOCALIP=%q\n' "$MYLOCALIP"
        printf './kytos-init.sh 2>&1 | tee %q\n' "$dir/e2e.log"
        # shellcheck disable=SC2016 # expanded by run.sh, not here
        printf 'rc=${PIPESTATUS[0]}; echo "$rc" > %q; exit "$rc"\n' "$dir/exit-code"
    } > "$dir/run.sh"

    tmux new-session -d -s "$TMUX_SESSION" "bash $dir/run.sh; exec bash"
    local _
    for _ in $(seq 1 20); do
        [ -f "$dir/pid" ] && break
        sleep 0.5
    done
    log "started run $ts in tmux: TESTS='$tests' RERUNS=$RERUNS"
    echo "$ts"
}

kytos_log_src() {
    if [ -s /var/log/user.log ]; then echo /var/log/user.log; else echo /var/log/syslog; fi
}

cmd_kytos_logs() {
    local src
    src=$(kytos_log_src)
    [ -f "$src" ] || die "no $src yet; kytosd has not logged anything"
    if [ "${1:-}" = "-f" ]; then
        tail -n "${LOG_LINES:-100}" -F "$src" 2>/dev/null | grep -a --line-buffered ' kytos\.'
    else
        grep -a ' kytos\.' "$src" | tail -n "${LOG_LINES:-100}"
    fi
}

# kytos lines logged between a run's start and end (syslog timestamps are UTC ISO)
run_kytos_log() {
    local dir=$1 start end=""
    start=$(cat "$dir/started" 2>/dev/null) || return 0
    [ -f "$dir/exit-code" ] && end=$(date -u -r "$dir/exit-code" +%Y-%m-%dT%H:%M:%S)
    awk -v s="$start" -v e="$end" \
        'index($0, " kytos.") && substr($1, 1, 19) >= s && (e == "" || substr($1, 1, 19) <= e)' \
        "$(kytos_log_src)" 2>/dev/null > "$dir/kytos.log" || true
}

cmd_logs() {
    [ -e "$RESULTS/latest/e2e.log" ] || die "no run yet"
    if [ "${1:-}" = "-f" ]; then
        tail -n +1 -F "$RESULTS/latest/e2e.log" 2>/dev/null &
        local tailpid=$!
        trap 'kill $tailpid 2>/dev/null' EXIT
        while ! run_finished; do
            if ! run_active; then
                sleep 1
                run_finished && break
                warn "run interrupted"
                exit 1
            fi
            sleep 2
        done
        sleep 1
        local rc
        rc=$(cat "$RESULTS/latest/exit-code")
        log "run finished with exit code $rc"
        exit "$rc"
    fi
    tail -n "${LOG_LINES:-100}" "$RESULTS/latest/e2e.log"
}

cmd_stop() {
    local pid pgid
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
    if run_active; then
        # in case the run outlived its tmux pane: stop its whole process group
        pid=$(cat "$RESULTS/latest/pid")
        pgid=$(ps -o pgid= -p "$pid" | tr -d ' ')
        if [ -n "$pgid" ] && [ "$pgid" != "$(ps -o pgid= -p $$ | tr -d ' ')" ]; then
            kill -TERM -- "-$pgid" 2>/dev/null || true
        fi
    fi
    pkill -f kytosd 2>/dev/null || true
    mn -c >/dev/null 2>&1 || true
    log "stopped"
}

cmd_bundle() {
    local id=${1:-latest} dir
    dir=$(readlink -f "$RESULTS/$id") || die "no run '$id'"
    [ -d "$dir" ] || die "no run '$id'"
    local id_dir
    id_dir=$(basename "$dir")
    run_kytos_log "$dir"
    tar -cf - --ignore-failed-read \
        --transform "s,^\(syslog\|dockerd.log\)\$,$id_dir/\1," \
        -C "$RESULTS" "$id_dir" -C /var/log syslog dockerd.log 2>/dev/null
}

case ${1:-} in
    provision|branch|run|logs|kytos_logs|stop|status|bundle)
        cmd=$1; shift; "cmd_$cmd" "$@" ;;
    manifest) manifest ;;
    *) die "usage: e2e-inside provision|branch|run|logs|kytos_logs|stop|status|bundle" ;;
esac
