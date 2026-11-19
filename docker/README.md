# Containers

From a clone, with Docker Compose 2.24+:

```sh
docker compose build
docker compose up -d
curl -fsS -H 'Host: localhost' http://127.0.0.1:8484/api/health
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: localhost' http://127.0.0.1:8484/mcp
docker compose logs worker
docker image inspect second-brain-builder-app:latest second-brain-builder-worker:latest --format '{{.RepoTags}} {{.Size}} bytes'
docker compose down -v
```

The MCP request should return 401. `down -v` deletes all instance data, Brain
clones, and cached models. Omit `-v` to preserve them. Both images use uid/gid
10001; new named volumes inherit the image directories' ownership. The app
mounts Brain clones and models read-only. Configuration is the optional root
`.env` described in `docs/architecture.md`; it is excluded from image builds.
Container storage paths stay `/data`, `/brains`, and `/models`.

CPU is the default. For NVIDIA hosts with the NVIDIA Container Toolkit, build
the GPU service and explicitly scale the CPU service to zero. Compose profiles
add services; they do not disable a service without a profile. The database
must have only one worker process:

```sh
docker compose --profile gpu build app worker-gpu
docker compose --profile gpu up -d --scale worker=0
docker compose --profile gpu logs worker-gpu
docker compose --profile gpu down
```

`SBB_CUDA_TORCH_INDEX` selects the CUDA wheel index (default `cu130`). The worker
Dockerfile also accepts `--build-arg TORCH_INDEX_URL=...`. CUDA builds resolve a
new lock inside the image because the committed lock points to CPU wheels.
The CPU build uses the committed lock unchanged. The host driver must support
the chosen CUDA version.

Release tags `v*` publish the CPU app and worker for amd64 and arm64 to
`ghcr.io/<owner>/<repository>-app:<tag>` and `-worker:<tag>`, plus `latest`.
Set `SBB_IMAGE_PREFIX=ghcr.io/<owner>/<repository>` and `SBB_IMAGE_TAG=<tag>`
to use these images with `docker compose pull` and `docker compose up -d --no-build`.
CUDA images are built locally with the GPU profile.
