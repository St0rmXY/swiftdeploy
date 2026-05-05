# SwiftDeploy

[![CI](https://github.com/St0rmXY/swiftdeploy/actions/workflows/ci.yml/badge.svg)](https://github.com/St0rmXY/swiftdeploy/actions/workflows/ci.yml)

A declarative container deployment CLI. `manifest.yaml` is the single source of truth — every config file is generated from it. Nothing is handwritten.

---

## Project Structure
swiftdeploy/
├── manifest.yaml              # Only file you edit
├── swiftdeploy                # CLI executable
├── Dockerfile                 # Builds the API service image
├── requirements.txt           # CLI dependencies (pyyaml, jinja2)
├── README.md
├── app/
│   └── main.py                # Python HTTP API
├── templates/
│   ├── nginx.conf.j2          # Generates nginx.conf
│   └── docker-compose.yml.j2  # Generates docker-compose.yml
├── tests/
│   ├── test_api.py            # API endpoint tests
│   └── test_cli.py            # CLI unit tests
└── .github/
└── workflows/
└── ci.yml             # GitHub Actions CI pipeline

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.12+ |
| Docker | 24+ |
| Docker Compose | v2 |

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/swiftdeploy.git
cd swiftdeploy

# 2. Install CLI dependencies
pip install -r requirements.txt

# 3. Make CLI executable
chmod +x swiftdeploy

# 4. Build the API image
docker build -t swift-deploy-1-node:latest .
```

---

## Subcommand Walkthrough

### `swiftdeploy init`
Parses `manifest.yaml` and generates `nginx.conf` and `docker-compose.yml` from templates.

```bash
./swiftdeploy init
```

---

### `swiftdeploy validate`
Runs 5 pre-flight checks before deploying:

1. `manifest.yaml` exists and is valid YAML
2. All required fields are present and non-empty
3. Docker image exists locally
4. Nginx port is not already bound on the host
5. Generated `nginx.conf` is syntactically valid

```bash
./swiftdeploy validate
```

---

### `swiftdeploy deploy`
Runs `init`, brings up the full stack, and blocks until health checks pass or 60s timeout.

```bash
./swiftdeploy deploy
```

Test after deploy:

```bash
curl http://localhost:8080/
curl http://localhost:8080/healthz
```

---

### `swiftdeploy promote <canary|stable>`
Switches deployment mode with a rolling restart of the app container only. Nginx stays live throughout.

```bash
# Switch to canary
./swiftdeploy promote canary

# Switch back to stable
./swiftdeploy promote stable
```

What it does:
1. Updates `mode` in `manifest.yaml` in-place
2. Regenerates `docker-compose.yml` with the new `MODE` env var
3. Restarts the app container only
4. Confirms the new mode by hitting `/healthz`

In canary mode:
- Every response includes `X-Mode: canary` header
- `POST /chaos` endpoint becomes active

---

### `swiftdeploy teardown`
Removes all containers, networks and volumes.

```bash
# Stop and remove stack
./swiftdeploy teardown

# Also delete generated config files
./swiftdeploy teardown --clean
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Welcome message with mode, version, timestamp |
| `GET` | `/healthz` | Returns status and uptime in seconds |
| `POST` | `/chaos` | Chaos injection (canary mode only) |

### Chaos Modes (canary only)

```bash
# Slow responses — sleep N seconds before replying
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode": "slow", "duration": 3}'

# Error injection — return 500 on ~50% of requests
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode": "error", "rate": 0.5}'

# Recover — cancel any active chaos
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode": "recover"}'
```

---

## Running Tests Locally

```bash
pip install -r requirements.txt pytest
pytest tests/ -v
```

No Docker required — API tests spin up an in-process server on port 3001.

---

## CI Pipeline

GitHub Actions runs automatically on every push and pull request:

1. Sets up Python 3.12
2. Installs dependencies from `requirements.txt`
3. Runs `tests/test_api.py` → uploads JUnit XML report
4. Runs `tests/test_cli.py` → uploads JUnit XML report
5. **Fails the build if any test fails**

To run the pipeline manually, push any commit to the repo.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODE` | `stable` | Deployment mode — `stable` or `canary` |
| `APP_VERSION` | `1.0.0` | App version string injected into responses |
| `APP_PORT` | `3000` | Port the API listens on inside the container |
| `TEST_PORT` | `3001` | Port used by the in-process test server |

---

## Security

- Containers run as non-root user
- All Linux capabilities dropped (`cap_drop: ALL`)
- Only required capabilities added back
- `no-new-privileges` enforced on all containers
- Service port never exposed directly to host — all traffic routes through Nginx
