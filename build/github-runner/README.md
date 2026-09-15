# GitHub Actions Self-Hosted Runner Deployment

This directory contains deployment artifacts for running a GitHub Actions self-hosted runner with Docker Compose on a local server.

- Target server (example): `192.168.16.202`
- Runtime: Docker + Docker Compose v2
- Runner image: `myoung34/github-runner:latest`

## Files

- `docker-compose.yml` - Runner container definition, volumes, resource limits, logging, and healthcheck
- `setup.sh` - Interactive setup script to generate `.env` and start services
- `.env.example` - Template of required and optional environment variables

## Prerequisites

1. Linux server with Docker Engine installed
2. Docker Compose plugin available (`docker compose version`)
3. Network access to GitHub (`github.com` or your GitHub Enterprise host)
4. Authentication prepared:
   - Repository runner: `RUNNER_TOKEN` or `ACCESS_TOKEN`
   - Organization/Enterprise runner: `ACCESS_TOKEN` with required admin scopes

## Quick Start

```bash
cd build/github-runner
cp .env.example .env
bash setup.sh
```

The setup script will:

1. Check Docker and Docker Compose prerequisites
2. Prompt for runner scope and GitHub target
3. Prompt for token configuration
4. Generate `.env`
5. Pull image and start runner container
6. Show status and next steps

## Configuration Instructions

You can either:

- Use `setup.sh` interactive prompts (recommended), or
- Edit `.env` manually from `.env.example`

### Scope Modes

1. **Repository scope**
   - `RUNNER_SCOPE=repo`
   - `REPO_URL=https://github.com/<org>/<repo>`
2. **Organization scope**
   - `RUNNER_SCOPE=org`
   - `ORG_NAME=<org>`
3. **Enterprise scope**
   - `RUNNER_SCOPE=enterprise`
   - `ENTERPRISE_NAME=<enterprise>`

### Token Selection

- `RUNNER_TOKEN`: Registration token from GitHub runner settings (short-lived)
- `ACCESS_TOKEN`: PAT for automatic registration (recommended for org/enterprise)

Do not commit `.env` to version control.

## Usage Commands

Run all commands from this directory (`build/github-runner`).

### Start

```bash
docker compose up -d
```

### Stop

```bash
docker compose down
```

### Logs

```bash
docker compose logs -f github-runner
```

### Restart

```bash
docker compose restart github-runner
```

### Update Image

```bash
docker compose pull
docker compose up -d
```

### Status

```bash
docker compose ps
```

## Troubleshooting

### Runner not visible in GitHub

1. Confirm token validity and scope in `.env`
2. Confirm one target mode is configured (`REPO_URL` or `ORG_NAME` or `ENTERPRISE_NAME`)
3. Inspect logs:
   ```bash
   docker compose logs --tail=200 github-runner
   ```

### Docker commands fail inside workflows

1. Verify Docker socket mount exists:
   ```bash
   docker compose exec github-runner ls -l /var/run/docker.sock
   ```
2. Ensure host Docker daemon is running

### Container repeatedly restarts

1. Check logs for registration errors
2. Validate runner scope/token pairing
3. Temporarily enable debug in `.env`:
   - `DEBUG_OUTPUT=true`
   - `DEBUG_ONLY=false`

### Permission/security notes

- Mounting `/var/run/docker.sock` grants high privileges to containerized jobs
- Use dedicated runner hosts for isolation
- Rotate tokens regularly and prefer short-lived registration tokens when possible
