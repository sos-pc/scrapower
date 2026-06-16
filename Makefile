.PHONY: deploy test lint typecheck build

# ── Déploiement production ──────────────────────────────
deploy: build
	scp -i ~/.ssh/clouscard-ghost.key src/scrapower/coordinator/static/worker.js ubuntu@130.110.242.56:~/scrapower/scrapower/coordinator/static/
	scp -i ~/.ssh/clouscard-ghost.key src/scrapower/coordinator/*.py ubuntu@130.110.242.56:~/scrapower/scrapower/coordinator/
	scp -i ~/.ssh/clouscard-ghost.key src/scrapower/coordinator/api/*.py ubuntu@130.110.242.56:~/scrapower/scrapower/coordinator/api/
	ssh -i ~/.ssh/clouscard-ghost.key ubuntu@130.110.242.56 "kill \$$(pgrep -f scrapower.coordinator | head -1) 2>/dev/null; sleep 2; cd ~/scrapower && SCRAPOWER_HOST=0.0.0.0 SCRAPOWER_API_KEY=sp-secure-key-2026 nohup .venv/bin/python -m scrapower.coordinator.main < /dev/null > scrapower.log 2>&1 & disown"
	@sleep 4
	@curl -sk https://scrapower.talos-int.com/health
	@echo ""
	@echo "✓ Déployé — https://scrapower.talos-int.com"

# ── Qualité ─────────────────────────────────────────────
test:
	.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_distribution.py --ignore=tests/test_gpu.py

lint:
	.venv/Scripts/python.exe -m ruff check src/ tests/

typecheck:
	.venv/Scripts/python.exe -m mypy src/scrapower --ignore-missing-imports

check: lint typecheck test
	@echo "✓ Tout est propre"

# ── Build ───────────────────────────────────────────────
build:
	@cd worker-browser && npx esbuild src/index.ts --bundle --format=esm --outfile=../src/scrapower/coordinator/static/worker.js
	@echo "✓ worker.js buildé"
