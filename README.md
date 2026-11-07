# German Audiobook Generator

Generate German audiobook MP3s from `.txt` files with one of three Thorsten-Voice engines:

- **CosyVoice3** — highest quality, but heavy; an NVIDIA GPU with sufficient VRAM is strongly recommended.
- **Kokoro** — good compromise, runs on the CPU in real time or faster, Docker only.
- **Piper** — fastest and lightest, runs locally as a CLI, ideal for weak hardware.

Each text file is synthesized, converted with ffmpeg, and written as an MP3 next to the source file.

## Features

- One MP3 per text file, using the same directory and filename stem.
- Engine selection with `--engine cosyvoice`, `--engine kokoro`, or `--engine piper`.
- CLI-configurable input: one or more text directories or text files, plus speaking speed, MP3 bitrate, and sample rate.
- Client-side German number spelling, enabled for CosyVoice by default and configurable with `--spell-numbers`.
- Optional CosyVoice normalization fields for compatible external servers; the bundled Thorsten-Voice image ignores them.
- Automatic Docker container management for CosyVoice and Kokoro, including model volume reuse.
- Automatic NVIDIA GPU usage for CosyVoice on Linux when a usable runtime or CDI is available; use `--gpu off` on GPUs with too little VRAM.
- CPU operation on Linux, macOS, and Apple Silicon.
- Only `.txt` files are converted. Existing non-empty MP3s are skipped unless `--force`/`--overwrite` is used.
- HTTP retries, WAV validation, atomic MP3 replacement, and clear error messages.

## Requirements

- Python 3.9 or newer.
- ffmpeg with `libmp3lame`.
- Docker for CosyVoice and Kokoro if the script should manage the container.
- For CosyVoice on Linux, optional: NVIDIA GPU with driver, NVIDIA Container Toolkit or CDI, and enough VRAM; 2 GB is insufficient.
- For Piper: a Piper executable and a German Piper voice model.
- The script itself uses only the Python standard library.

Install ffmpeg if needed:

```bash
sudo apt install ffmpeg
```

On macOS:

```bash
brew install ffmpeg
```

## Layout

- Project files live in the repository root.
- `tts_audiobook.py` is the executable generator.
- Generated MP3s are written next to their source `.txt` files.

## Engine Overview

| Engine | Quality | Hardware | Backend | First-run cost |
| --- | --- | --- | --- | --- |
| `cosyvoice` | Highest | NVIDIA GPU with sufficient VRAM strongly recommended; CPU works but is slow | Docker | ~19 GB image + ~9 GB models |
| `kokoro` | Good | CPU, real time or faster | Docker | A few hundred MB model data |
| `piper` | Good, lightweight | CPU, very fast | Local Piper CLI | Model file, usually 20–120 MB |

A 2 GB GPU is not sufficient for CosyVoice3 and fails with CUDA out of memory. On a weak GPU, a laptop GPU with little VRAM, or no GPU, use `--gpu off` with CosyVoice, or use Kokoro or Piper. CosyVoice on CPU can take minutes per short text.

## Quick Start with CosyVoice

```bash
./tts_audiobook.py --text-dir path/to/texts
```

`--text-dir` and `--text-file` are aliases for the same input option, which accepts one or more paths. Each path may be a directory or a text file, and the flags may be repeated. Duplicate files are processed only once. `--glob` and `--recursive` apply to directory inputs; they are rejected when every input is an explicit file. The input option is required; if it is omitted, the script prints an error and exits without starting Docker or the TTS server.

The first run pulls the Docker image (about 19 GB on disk after extraction) and downloads about 9 GB of pretrained models. Depending on the connection this can take 15 minutes to several hours. The models are cached in the Docker volume `cosyvoice_models`. Later starts are much faster.

If Docker bridge networking is broken on the host, add `--docker-network host` and keep the default port `8000`. On a GPU with only 2 GB VRAM, add `--gpu off`.

## Kokoro

Kokoro is the recommended choice when CosyVoice is too slow or the GPU has too little memory.

```bash
./tts_audiobook.py --engine kokoro --text-dir path/to/texts
```

The script starts or reuses the container `thorsten-kokoro-tts` from `thorstenvoice/kokoro-tts:latest`, with the model cache in the Docker volume `kokoro_cache`.

Kokoro runs on the CPU. `--gpu on` is rejected; `--gpu auto` simply uses the CPU. The container is multi-arch and works on Linux, macOS Intel, and Apple Silicon.

Choose a different Kokoro training checkpoint:

```bash
./tts_audiobook.py --engine kokoro --text-dir path/to/texts --kokoro-epoch 10
```

If the Kokoro container already exists, add `--recreate`. The script refuses to reuse a container whose `KOKORO_EPOCH` does not match the requested epoch.

Change the host port:

```bash
./tts_audiobook.py --engine kokoro --text-dir path/to/texts --port 9000
```

Kokoro listens on port `8000` inside the container; `--port 9000` maps host port `9000` to it.

## Piper

Piper is the fastest and lightest option. It runs locally and does not use Docker.

### Install Piper

The recommended installation is a virtual environment:

```bash
python3 -m venv .venv-piper
.venv-piper/bin/pip install piper-tts
```

Then pass the venv executable explicitly:

```bash
--piper-binary .venv-piper/bin/piper
```

Alternatively, activate the venv so `piper` is on `PATH`, or install `piper-tts` with `pipx`. The script automatically uses `piper` from `PATH` or, if that is unavailable, `python3 -m piper`.

If you use the standalone Piper release from the Thorsten-Voice instructions, keep the complete extracted directory together and pass the executable explicitly:

```bash
./tts_audiobook.py --engine piper \
  --piper-binary tools/piper/piper \
  --piper-model models/de_DE-thorsten-high.onnx \
  --text-dir path/to/texts
```

### Download a German voice

Download the `.onnx` model and its `.onnx.json` configuration into the same directory. For example, the neutral high German Thorsten voice:

```bash
mkdir -p models
curl -L -o models/de_DE-thorsten-high.onnx \
  "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx?download=true"
curl -L -o models/de_DE-thorsten-high.onnx.json \
  "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json"
```

Then run:

```bash
./tts_audiobook.py --engine piper \
  --piper-model models/de_DE-thorsten-high.onnx \
  --text-dir path/to/texts
```

The model and configuration must keep the same base name; the script verifies that `<model>.json` exists.

### Piper speed and speakers

`--speed` is mapped to Piper's inverse length scale. For example, `--speed 0.8` becomes `--length_scale 1.25`. If you need direct control:

```bash
./tts_audiobook.py --engine piper \
  --piper-model models/de_DE-thorsten-high.onnx \
  --piper-length-scale 1.1 \
  --text-dir path/to/texts
```

For the emotional Piper model, select a speaker:

```bash
./tts_audiobook.py --engine piper \
  --piper-model models/de_DE-thorsten_emotional-medium.onnx \
  --piper-speaker 1 \
  --text-dir path/to/texts
```

Do not pass `--piper-speaker` for the high German or Hessian models.

Piper reloads the model once per chapter. This is still fast, but for very large projects a Piper HTTP server can be faster.

## What Happens

1. The script resolves the input paths and collects the matching text files.
2. For CosyVoice and Kokoro, it checks the server's `/health` endpoint.
3. If the server is not reachable and `--docker auto` is active, it starts or reuses the engine-specific container.
4. Before synthesis, numbers are optionally spelled out in German; `--spell-numbers auto` enables this for CosyVoice and leaves Kokoro and Piper unchanged.
5. Each chapter is sent to the selected engine:
   - CosyVoice: multipart form request to `/tts`
   - Kokoro: JSON request to `/tts`
   - Piper: local subprocess with the text on standard input
6. The returned WAV is converted to MP3 with ffmpeg and atomically moved next to the source file.
7. After each file, the script prints its playback duration, file size, and render time. With `--cooldown`, it then pauses before continuing; the pause also runs after the last generated file.
8. Failed chapters are reported, remaining chapters continue, and the process exits non-zero if anything failed.
9. The managed Docker container is stopped on exit, including on errors and Ctrl+C. An external server used with `--docker never` is left untouched.

## Examples

Run these from the project root.

Generate only one chapter:

```bash
./tts_audiobook.py --text-file path/to/chapter.txt
```

Generate several selected chapters:

```bash
./tts_audiobook.py --text-file \
  path/to/chapter-01.txt \
  path/to/chapter-02.txt
```

Use Piper for a fast CPU run:

```bash
./tts_audiobook.py --engine piper \
  --piper-model models/de_DE-thorsten-high.onnx \
  --text-dir path/to/texts
```

Use Kokoro with a slower speaking speed:

```bash
./tts_audiobook.py --engine kokoro --text-dir path/to/texts --speed 0.9
```

When its WeText frontend is available, the Thorsten-Voice CosyVoice image handles German numbers with English words. The client therefore spells numbers in German before sending text to CosyVoice by default: `C96` becomes `C sechsundneunzig`, `1887` becomes `achtzehnhundertsiebenundachtzig`, and `7,63` becomes `sieben Komma sechs drei`. Use `--spell-numbers off` to send digits unchanged, or `--spell-numbers de` to apply the same preprocessing to Kokoro or Piper.

The client still accepts `--normalize-text`, `--normalize-loudness`, and `--lufs-target` for compatible external servers, but the bundled image currently reads only `text` and `speed` and ignores those fields.

Preview without starting Docker or contacting the server:

```bash
./tts_audiobook.py --engine kokoro --text-dir path/to/texts --dry-run
```

Regenerate all MP3s, including existing ones:

```bash
./tts_audiobook.py --text-dir path/to/texts --force
# --overwrite is an alias for --force
```

Pause for two minutes after each generated chapter:

```bash
./tts_audiobook.py --text-dir path/to/texts --cooldown 120
```

Use an already running server without managing Docker:

```bash
./tts_audiobook.py --engine kokoro --text-dir path/to/texts \
  --docker never --server-url http://127.0.0.1:8000
```

Require CosyVoice GPU usage and rebuild an existing container if necessary:

```bash
./tts_audiobook.py --text-dir path/to/texts --gpu on --recreate
```

The managed container is stopped automatically after generation, including on Ctrl+C. If you want to keep a server running, start it outside the script and use `--docker never --server-url http://127.0.0.1:8000`.

## Important Options

| Option | Default | Description |
| --- | --- | --- |
| `--text-dir PATH [PATH ...]`, `--text-file PATH [PATH ...]` | required | One or more directories or text files; may be repeated. |
| `--glob PATTERN` | `*.txt` | File selection pattern for directory inputs. |
| `--recursive` | off | Search subdirectories of directory inputs. |
| `--force`, `--overwrite` | off | Regenerate MP3s even when they already exist. |
| `--dry-run` | off | Show planned actions without contacting Docker or the server. |
| `--cooldown SECONDS` | `0` | Pause after each generated MP3, including the last, to let the system cool down. |
| `--engine ENGINE` | `cosyvoice` | `cosyvoice`, `kokoro`, or `piper`. |
| `--speed FLOAT` | `1.0` | Speaking speed from `0.5` to `2.0`; Piper maps it to length scale. |
| `--spell-numbers auto\|de\|off` | `auto` | Spell numbers before synthesis; `auto` uses German for CosyVoice. `de` also applies to Kokoro and Piper; `off` leaves digits unchanged. |
| `--kokoro-epoch N` | `5` | Kokoro training checkpoint from `1` to `10` for a managed container. |
| `--piper-binary PATH` | auto | Piper executable; otherwise `piper` or `python3 -m piper`. |
| `--piper-model PATH` | required for Piper | Piper `.onnx` model. |
| `--piper-speaker N` | unset | Speaker id for multi-speaker Piper models. |
| `--piper-length-scale FLOAT` | unset | Direct Piper length scale; overrides `--speed`. |
| `--normalize-text` | off | Sent as `normalize_text`; ignored by the Thorsten-Voice image. |
| `--normalize-loudness` | off | Sent as `normalize_loudness`; ignored by the Thorsten-Voice image. |
| `--lufs-target FLOAT` | `-23.0` | Sent as `lufs_target`; used only by compatible CosyVoice servers. |
| `--server-url URL` | `http://127.0.0.1:8000` | Existing CosyVoice or Kokoro server. |
| `--port PORT` | `8000` or URL port | Docker host port. |
| `--timeout SECONDS` | `7200` | Per-chapter HTTP timeout. |
| `--retries N` | `2` | Retry transient HTTP failures. |
| `--start-timeout SECONDS` | `1800` | How long to wait for a newly started container to become healthy. |
| `--mp3-bitrate RATE` | `192k` | MP3 bitrate. |
| `--mp3-sample-rate HZ` | `44100` | MP3 sample rate. |
| `--album TEXT` | empty | Optional album ID3 tag. |
| `--artist TEXT` | `Thorsten-Voice` | Artist ID3 tag. |
| `--no-tags` | off | Disable ID3 tags. |
| `--docker auto\|never` | `auto` | Manage a local container or require an external server. |
| `--docker-network NETWORK` | unset | Docker network mode for a managed container; use `host` if bridge networking is broken. |
| `--gpu auto\|on\|off` | `auto` | CosyVoice GPU selection; use `off` on GPUs with insufficient VRAM (2 GB is too small). Kokoro and Piper are CPU-only. |
| `--image IMAGE` | engine-specific | Docker image override. |
| `--container-name NAME` | engine-specific | Managed container name override. |
| `--model-volume NAME` | engine-specific | Model cache volume override. |
| `--recreate` | off | Explicitly remove and recreate the managed container; this is the only case where a running container is restarted. |
| `--stop-container` | on | Accepted for compatibility; managed containers are always stopped on exit. |

Run `./tts_audiobook.py --help` for the complete list.

## Output Files

For a source file `path/to/chapter.txt`, the output is `path/to/chapter.mp3`.

Unless `--no-tags` is used, the MP3 receives ID3 tags:

- title derived from the filename, without the numeric prefix
- track number derived from the numeric filename prefix
- artist `Thorsten-Voice`
- album from `--album`, when set

The source WAV is temporary and removed automatically. The MP3 is first written to a temporary file and then atomically renamed, so an interrupted conversion does not leave a partially written final MP3.

## GPU Support for CosyVoice on Linux

The script uses the GPU only when all of the following are true:

- the engine is CosyVoice
- `--gpu` is not `off`
- the platform is Linux
- `nvidia-smi -L` succeeds
- the Docker daemon has an `nvidia` runtime or an NVIDIA CDI specification

Then the managed container is started with `--gpus all`. Newer Docker versions can use the NVIDIA CDI specification without a separate `nvidia` runtime.

If the GPU exists but is not usable, `--gpu auto` falls back to CPU with a warning. This check does not consider VRAM; it can select a GPU that is too small. A 2 GB GPU is not sufficient for CosyVoice3 and fails while loading the model with CUDA out of memory. Use `--gpu off` in that case. `--gpu on` aborts with diagnostics instead of silently using the CPU.

If Docker cannot access the GPU, configure the NVIDIA runtime and CDI specification:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
sudo systemctl restart docker
```

If `nvidia-smi` reports a driver/library mismatch, reboot the machine or reload the NVIDIA kernel modules. If that does not help, reinstall the NVIDIA driver.

An existing CPU-only container is not modified automatically. Use `--recreate` to rebuild it with GPU access. This keeps the model volume, so the models do not need to be downloaded again. Conversely, an existing GPU container is reused even with `--gpu off`; add `--recreate` to rebuild it for CPU.

Kokoro and Piper do not use the NVIDIA GPU through this script.

## Container Lifecycle

- If a managed container is already running, the script reuses it and does not restart it. `--recreate` is the explicit exception and replaces the container.
- The managed container is stopped when the script exits: normal completion, errors, Ctrl+C, SIGTERM, or SIGHUP. This also applies when the script reused an already running container.
- An external server used with `--docker never` is not stopped or otherwise managed.
- SIGKILL or a power loss cannot be caught; in those cases the container may keep running.

## macOS

Kokoro is published as a multi-arch image and runs natively on Apple Silicon. Piper also runs natively.

CosyVoice CPU generation works with Docker Desktop, but is much slower; the Thorsten-Voice documentation reports roughly 16 times slower CPU inference compared with an RTX 4090. On Apple Silicon the CosyVoice image may run through emulation.

## Kubernetes

The CosyVoice image runs as root inside the container by default because it does not set a `USER`. It does not need host root, `privileged`, host networking, or extra capabilities. Kubernetes can run it as an unprivileged user with `runAsNonRoot`, `runAsUser`, and writable volumes for the models and caches.

The image writes to these paths:

- `/app/CosyVoice/pretrained_models` for the models
- `/root/.cache` by default; redirect this to a writable path

The following example runs as UID/GID `1000`, uses a 20 GiB model PVC and a 5 GiB cache PVC, and keeps the root filesystem read-only. Adjust storage, CPU, and memory to your cluster.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: cosyvoice-models
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 20Gi
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: cosyvoice-cache
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 5Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cosyvoice
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: cosyvoice
  template:
    metadata:
      labels:
        app: cosyvoice
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: cosyvoice
          image: thorstenvoice/cosyvoice-tts:cosyvoice3
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8000
          env:
            - name: HOME
              value: /data
            - name: HF_HOME
              value: /data/huggingface
            - name: MODELSCOPE_CACHE
              value: /data/modelscope
            - name: XDG_CACHE_HOME
              value: /data/.cache
            - name: TORCH_HOME
              value: /data/torch
            - name: TMPDIR
              value: /tmp
            - name: PYTHONDONTWRITEBYTECODE
              value: "1"
            - name: PORT
              value: "8000"
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: models
              mountPath: /app/CosyVoice/pretrained_models
            - name: cache
              mountPath: /data
            - name: tmp
              mountPath: /tmp
            - name: shm
              mountPath: /dev/shm
          startupProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 1800
          resources:
            requests:
              cpu: "2"
              memory: 12Gi
            limits:
              cpu: "8"
              memory: 24Gi
      volumes:
        - name: models
          persistentVolumeClaim:
            claimName: cosyvoice-models
        - name: cache
          persistentVolumeClaim:
            claimName: cosyvoice-cache
        - name: tmp
          emptyDir: {}
        - name: shm
          emptyDir:
            medium: Memory
            sizeLimit: 1Gi
---
apiVersion: v1
kind: Service
metadata:
  name: cosyvoice
spec:
  selector:
    app: cosyvoice
  ports:
    - name: http
      port: 8000
      targetPort: http
```

Save the manifest as `cosyvoice-k8s.yaml` and apply it:

```bash
kubectl apply -f cosyvoice-k8s.yaml
```

Important details:

- `runAsUser` is required. Without it, `runAsNonRoot: true` rejects the image because the image does not set a `USER`.
- `fsGroup` makes the PVCs writable for UID 1000. The storage class must support `fsGroup`; with NFS or `hostPath`, chown the directories to 1000 or use a storage class that applies the permissions.
- The first start downloads about 9 GB of models. The startup probe allows up to 5 hours. Pre-populate the model PVC if you want to avoid the download on a new volume.
- The `/tts` handler runs synchronously and can block `/health` during synthesis. Do not use an aggressive liveness probe. Use the generous startup probe and check the logs or `/health` manually, or use a TCP or exec probe.
- For GPU access, add `nvidia.com/gpu: 1` to `resources.limits`, ensure the node GPU has enough VRAM (2 GB is insufficient), and make sure the NVIDIA device plugin supports non-root containers. Do not add `privileged: true`.
- If Hugging Face Xet downloads stall in the cluster, set `HF_HUB_DISABLE_XET=1` in the container environment.
- Use the script against the Service:

  ```bash
  kubectl port-forward svc/cosyvoice 8000:8000
  ./tts_audiobook.py --text-dir path/to/texts \
    --docker never --server-url http://127.0.0.1:8000
  ```

- If the first model download is interrupted, the `config.json` marker behavior described in Troubleshooting applies. Keep the PVC and remove the marker, or recreate the volume.

## Performance

- **CosyVoice**: highest quality, but compute-heavy. Use a GPU with enough VRAM if possible; otherwise use `--gpu off`. CPU inference can take several minutes per short text and much longer per chapter.
- **Kokoro**: good quality and fast on CPU, usually real time or faster.
- **Piper**: fastest and lightest, especially suitable for older laptops, Raspberry Pi, and long audiobooks.

The script prints progress per file, including playback duration, final file size, and render time. It cannot report progress inside a single long CosyVoice `/tts` request. Use `--cooldown SECONDS` to pause after each generated chapter on a CPU-only or thermally limited system.

## Troubleshooting

### Docker permission denied

Add your user to the Docker group and log in again:

```bash
sudo usermod -aG docker "$USER"
```

### Docker daemon is not reachable

Start Docker Engine or Docker Desktop and verify:

```bash
docker info
```

### The first CosyVoice run appears to hang

The first container start downloads about 9 GB of models after the image pull. The exact on-disk size can be larger because the base model repository also contains extra checkpoints; use `/health` or the log line, not the size, to detect completion. Watch the container logs:

```bash
docker logs -f cosyvoice-tts
```

Kokoro's first start downloads a few hundred MB into `kokoro_cache`:

```bash
docker logs -f thorsten-kokoro-tts
```

The script also waits for `/health`, detects repeated container restarts, and prints recent logs if the container stops or the start timeout expires.

### The first CosyVoice model download was interrupted

Ctrl+C stops the script and the managed container. The container is started detached with `--restart unless-stopped`, but the script's cleanup prevents it from continuing in the background. Re-running the same command starts or reuses it again.

Stopping or restarting the container during the initial download, including a Docker or host restart, remains risky. The CosyVoice image treats the base model as complete as soon as this file exists:

```text
/app/CosyVoice/pretrained_models/CosyVoice3-0.5B/CosyVoice-BlankEN/config.json
```

That file is written early in the download. If the container restarts while the large model files are still incomplete, the entrypoint can skip the rest of the base-model download, and the server may fail or restart repeatedly. The container itself is fine; the `cosyvoice_models` volume contains an incomplete model.

To resume and keep the already downloaded data, remove only that marker file so the entrypoint runs `hf download` again. Hugging Face resumes the existing `.incomplete` files:

```bash
docker stop cosyvoice-tts
docker run --rm \
  -v cosyvoice_models:/app/CosyVoice/pretrained_models \
  --entrypoint sh thorstenvoice/cosyvoice-tts:cosyvoice3 \
  -c 'rm -f /app/CosyVoice/pretrained_models/CosyVoice3-0.5B/CosyVoice-BlankEN/config.json'
./tts_audiobook.py --text-dir path/to/texts --start-timeout 14400
```

If the cached data is unusable or you want a clean start, remove the container and the volume and download everything again:

```bash
docker rm -f cosyvoice-tts
docker volume rm cosyvoice_models
./tts_audiobook.py --text-dir path/to/texts --start-timeout 14400
```

`--recreate` rebuilds the container but keeps the model volume, so it does not fix an incomplete volume.

On a slow connection the default `--start-timeout 1800` can expire before the ~9 GB download finishes. When that happens the script exits and stops the detached container. Re-run with a larger value such as `--start-timeout 14400`, or simply run the script again; the model volume keeps completed data. Stopping during the first download can leave an incomplete model volume, as described below.

While the script is waiting, check progress from a second terminal with:

```bash
docker exec cosyvoice-tts du -sh /app/CosyVoice/pretrained_models
```

This includes completed files and partial `.incomplete` downloads, so it is a good estimate of how much of the ~9 GB is already downloaded. The partial cache alone:

```bash
docker exec cosyvoice-tts du -sh /app/CosyVoice/pretrained_models/CosyVoice3-0.5B/.cache
```

Watch it update every 30 seconds:

```bash
watch -n 30 'docker exec cosyvoice-tts du -sh /app/CosyVoice/pretrained_models'
```

Per-file progress is visible in the container log:

```bash
docker logs -f cosyvoice-tts
```

Use the image, container, and volume names you passed to `--image`, `--container-name`, or `--model-volume` if you overrode the defaults.

### CosyVoice returns HTTP 500 with an empty error

The Thorsten-Voice image's WeText normalizer can return an empty result for text that contains blank lines and digits. The script collapses blank lines to spaces before sending CosyVoice requests. If you use your own client, do the same or remove blank lines from the text.

### Kokoro cannot reach huggingface.co or the host port

The first Kokoro start needs outbound internet. If the logs contain `Temporary failure in name resolution`, the container has no working DNS or outbound route. Some Docker bridge setups also fail to publish ports, so the container is healthy internally but `http://127.0.0.1:8000/health` times out on the host.

Use host networking for the managed container:

```bash
./tts_audiobook.py --engine kokoro --docker-network host \
  --text-dir path/to/texts
```

With host networking, Kokoro listens directly on host port `8000`; keep the default `--port 8000`. The model is cached in `kokoro_cache`. Add `--recreate` when switching an existing container from bridge to host networking.

The same flag works for CosyVoice when its initial model download cannot reach the internet through the Docker bridge.

### The container exists on a different port

Use `--recreate` so the managed container is rebuilt with the requested port or network mode. The model volume is reused.

### The server is reachable but CosyVoice does not use the GPU

Check the container configuration and recreate it if necessary:

```bash
./tts_audiobook.py --text-dir path/to/texts --gpu on --recreate
```

For an externally managed server, the script cannot verify or control GPU usage.

### CosyVoice runs out of GPU memory

The script checks whether a GPU is usable, not whether it has enough VRAM. A 2 GB GPU is not sufficient for CosyVoice3. Use CPU. If the existing container was created with GPU access, rebuild it:

```bash
./tts_audiobook.py --text-dir path/to/texts --gpu off --recreate
```

The model volume is preserved; other caches, such as the WeText cache, are not mounted by the script and may need to be downloaded again.

### CosyVoice reads numbers in English

When its WeText frontend is available, the bundled image uses the English number normalizer for German text, so `1887` may become `one thousand, eight hundred and eighty-seven` and `C96` may become `Cninety-six`. The client pre-spells numbers in German for CosyVoice by default. Use `--spell-numbers off` to send digits unchanged, or `--spell-numbers de` to enable the same preprocessing for Kokoro or Piper.

If the container log says `no frontend is avaliable`, the image could not load WeText. Client-side number spelling still works, but other server-side text normalization may be unavailable.

### CosyVoice normalization options have no effect

The script sends `normalize_text`, `normalize_loudness`, and `lufs_target`, but the Thorsten-Voice image's `/tts` handler reads only `text` and `speed`. Use a compatible external server if you need those fields.

### Piper was not found

Install it in a virtual environment:

```bash
python3 -m venv .venv-piper
.venv-piper/bin/pip install piper-tts
```

Then pass `.venv-piper/bin/piper` with `--piper-binary`, or activate the venv first.

### Piper model config is missing

Piper needs both files with matching names:

```text
de_DE-thorsten-high.onnx
de_DE-thorsten-high.onnx.json
```

### ffmpeg is missing

Install it with `sudo apt install ffmpeg` on Linux or `brew install ffmpeg` on macOS.

## Reference

- Thorsten-Voice instructions:
  - CosyVoice: <https://www.thorsten-voice.de/anleitungen/cosyvoice/>
  - Kokoro: <https://www.thorsten-voice.de/anleitungen/kokoro/>
  - Piper: <https://www.thorsten-voice.de/anleitungen/piper/>
- Docker images:
  - CosyVoice: <https://hub.docker.com/r/thorstenvoice/cosyvoice-tts>
  - Kokoro: <https://hub.docker.com/r/thorstenvoice/kokoro-tts>
- Piper: <https://github.com/OHF-Voice/piper1-gpl>
