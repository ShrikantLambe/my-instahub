.PHONY: up down bucket api-install migrate seed api test web-install web reset loadtest feedtest reconcile

up:            ## Start Postgres, Redis and the local S3 (moto), then create the bucket
	docker compose up -d --wait
	$(MAKE) bucket

bucket:        ## Create the local bucket with CORS + public read (re-run if the s3 container restarts)
	cd api && uv run python -m scripts.setup_local_bucket

down:          ## Stop Postgres + Redis
	docker compose down

api-install:   ## Install API deps into api/.venv
	cd api && uv sync

migrate:       ## Apply SQL migrations
	cd api && uv run python -m scripts.migrate

seed:          ## Seed users A, B, C with follows and sample posts
	cd api && uv run python -m scripts.seed

api:           ## Run the API on :8000 with reload
	cd api && uv run uvicorn app.main:app --reload --port 8000

test:          ## Run API tests (needs Postgres up)
	cd api && uv run pytest -q

loadtest:      ## Load-test one hot post in direct and buffered mode (needs k6; local DB only)
	cd api && uv run python -m scripts.loadtest $(ARGS)

feedtest:      ## Compare home feed fan-out on read vs write (needs k6; local DB only)
	cd api && uv run python -m scripts.feedtest $(ARGS)

reconcile:     ## Recount likes from the likes table and repair drifted counters
	cd api && uv run python -m scripts.reconcile

web-install:   ## Install web deps
	cd web && npm install

web:           ## Run the web app on :3000
	cd web && npm run dev

reset:         ## Wipe the database volume and start fresh
	docker compose down -v && $(MAKE) up migrate seed
