## Running Kytos end-to-end tests with P4OfSwitch

Runs the tests against [P4OfSwitch](https://hub.docker.com/r/amlight/p4ofswitch) (Tofino model) switches, using a [mininet fork](https://github.com/italovalcy/mininet/releases/tag/2.3.2) with Docker hosts. `scripts/p4ofswitch/p4e2e` sets everything up, on a local Docker daemon or on Kubernetes.

**Requirements:** `docker` (or `kubectl` with `BACKEND=k8s`), and a native amd64 Docker host or cluster: the Tofino model crashes under emulation, so Docker Desktop on Apple Silicon cannot run the switches. From a Mac, use `BACKEND=k8s` or a remote amd64 host (`DOCKER_CONTEXT=<context> p4e2e up`). Huge pages: `up` raises `vm.nr_hugepages` to `HUGEPAGES` (2048) from the privileged kytos container, which changes the host kernel setting. Each switch uses about 81 pages of 2MB.

Put it on your `PATH` once, from anywhere in the repo; it then works from any directory:

```bash
mkdir -p ~/.local/bin
ln -sf "$(git rev-parse --show-toplevel)/scripts/p4ofswitch/p4e2e" ~/.local/bin/
```

```bash
# start mongo, kafka and kytos, and provision mininet, docker and the switch image
p4e2e up

# optionally parametrize kytos-end-to-end-tests branch and other NApp branches
p4e2e branch e2e=master telemetry_int=master

# run and follow; exits with pytest's exit code
p4e2e run -f --reruns 0 tests/test_e2e_96_telemetry_int.py -k test_350_proxy

# tail pytest logs
p4e2e logs -f

# tail kytosd logs
p4e2e logs -f --kytos

# optionally copy the logs to <repo>/e2e-results/<run-id>/
p4e2e fetch

p4e2e down
```

| Command | |
|---|---|
| `up [--recreate] [--e2e-branch REF] [name=ref ...]` | Create and provision the environment. Safe to re-run. `--recreate` replaces the kytos container/pod. |
| `branch [--force] name=ref ...` | Check out other branches, see below. |
| `run [-f] [--attach] [--reruns N] [pytest args]` | Run `kytos-init.sh` in tmux (default `tests/`). `-f` follows and exits with pytest's code. Arguments cannot contain spaces: use node ids or a one-word `-k`. |
| `logs [-f] [--kytos]` / `attach` / `stop` | Tail the latest run, or the kytosd log with `--kytos` / attach to tmux (`Ctrl-b d` detaches) / stop it. |
| `fetch [RUN_ID]` | Copy a run: test log, exit code, `kytos.log` (kytosd lines from that run only), full syslog, commits of every NApp, pip freeze. |
| `status` / `shell` / `config` | Show the environment / open a shell in kytos / print the settings. |
| `down [--keep-infra] [--purge]` | Remove everything; keep mongo and kafka / also delete the cached switch image. |
| `build-image -t TAG [--push] [name=ref ...]` | Build a kytos image with other branches. |

### Configuration

Override settings with `KEY=value` lines in `scripts/p4ofswitch/.env.local`, or environment variables (which win). `p4e2e config` lists every setting with its effective value. The main ones:

| Variable | Default |
|---|---|
| `BACKEND` | `docker` (or `k8s`) |
| `KYTOS_IMAGE` | `amlight/kytos:latest` |
| `E2E_BRANCH` | `master`, the branch of this repo the tests run from |
| `P4OFSWITCH_IMAGE` | `amlight/p4ofswitch:latest` |
| `P4OFSWITCH_ARCH` | `tf1` (64 x 100G) or `tf2` (32 x 400G) |
| `MYLOCALIP` | auto; the address switch containers use to reach kytos |
| `RERUNS` | `2` |
| `PREFIX` / `K8S_DEPLOYMENT` | `e2e-p4` / `kytos-regression-tests`; set another one to run environments side by side (docker / k8s) |
| `KUBECONFIG`, `K8S_NAMESPACE` | `~/.kube/config`, the context's namespace |

### Testing branches

```bash
p4e2e branch mef_eline=fix/foo
p4e2e branch mef_eline=myuser/mef_eline#fix/foo
p4e2e branch e2e=feat/x kytos=fix/y
```

`branch` only swaps code; it never installs Python packages. If a branch's `requirements/run.txt` needs a package or version the image lacks, it refuses and lists them. For those branches, build an image instead (below). Local changes in a checkout block `branch` unless you pass `--force`; the `settings.py` edits `kytos-init.sh` makes are reset automatically.

### Custom kytos image

```bash
# kytos-ng branches only
p4e2e build-image -t docker.io/<user>/kytos-e2e:dev --push telemetry_int=master
KYTOS_IMAGE=docker.io/<user>/kytos-e2e:dev p4e2e up
```

- **Pulling:** `user/repo:tag` is always pulled. If the pull fails, docker falls back to a local copy. A digest is pulled only if missing, and a bare `name:tag` is treated as local only. Override with `KYTOS_PULL=always|missing|never`.
- **New push of the same tag:** run `p4e2e up --recreate` to switch to it.
- **Docker backend:** a local build works without pushing.
- **Kubernetes:** nodes pull the image themselves, so a remote cluster needs it pushed to a registry it can reach (Docker Hub works).
