.PHONY: deploy test lint typecheck build docker-build docker-up docker-down docker-logs

# ── Déploiement production ─────────────────────────────────────
SSH_KEY := ~/.ssh/clouscard-ghost.key
SERVER  := ubuntu@130.110.242.56

deploy: build docker-build
	scp -i $(SSH_KEY) docker-compose.yml .env $(SERVER):~/scrapower/
	scp -i $(SSH_KEY) Dockerfile $(SERVER):~/scrapower/
	scp -i $(SSH_KEY) pyproject.toml $(SERVER):~/scrapower/
	scp -r -i $(SSH_KEY) src/ $(SERVER):~/scrapower/
	scp -r -i $(SSH_KEY) worker-browser/ $(SERVER):~/scrapower/
	ssh -i $(SSH_KEY) $(SERVER) "cd ~/scrapower && docker compose down 2>/dev/null; docker compose up -d --build"
	@sleep 5
	@curl -sk https://scrapower.talos-int.com/health
	@echo ""
	@echo "✓ Déployé — https://scrapower.talos-int.com"

# ── Qualité ────────────────────────────────────────────────────
test:
	.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_distribution.py --ignore=tests/test_gpu.py

lint:
	.venv/Scripts/python.exe -m ruff check src/ tests/

typecheck:
	.venv/Scripts/python.exe -m mypy src/scrapower --ignore-missing-imports

check: lint typecheck test
	@echo "✓ Tout est propre"

# ── Build ──────────────────────────────────────────────────────
build:
	@cd worker-browser && npm run build
	@echo "✓ worker.js + sandbox_worker.js buildés"

# ── Docker (local dev) ─────────────────────────────────────────
docker-build:
	docker build -t scrapower .

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f
