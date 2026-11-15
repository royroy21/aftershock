.PHONY: \
    up \
    down \
    build \
    logs \
    shell \
    manage \
    migrate \
    migrations \
    test \
    lint \
    ingest \
    explain \

usage:
	@echo "up..............................docker compose up"
	@echo "down............................docker compose down"
	@echo "build...........................docker compose build"
	@echo "logs............................docker compose logs -f"
	@echo "shell...........................Django's shell plus command"
	@echo "manage..........................Django's manage command"
	@echo "migrate.........................Run Django migrations"
	@echo "migrations......................Create Django migrations"
	@echo "test............................Run Django tests"
	@echo "lint............................Ruff Linting"
	@echo "lint............................Ruff Linting"


	@echo "Available commands:"
	@echo "bash............................Enter django server with bash shell prompt"
	@echo "chown...........................Change ownership of files to own user"
	@echo "clear_cache.....................Clears redis cache"
	@echo "clear_pyc.......................Remove all pyc files"
	@echo "help............................Display available commands"
	@echo "manage..........................Django's manage.py command"
	@echo "migrate.........................Run Django migrations"
	@echo "migrations......................Create Django migrations"
	@echo "psql............................Connect to postgis server"
	@echo "shell...........................Django's shell plus command"
	@echo "test............................Run tests for the project"
	@echo "ingest..........................Ingest quake events"
	@echo "explain.........................Custom Django management command to explain queries"

up:
	docker compose up --build

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

shell:
	docker compose exec web python manage.py shell

manage:
	docker compose run --rm ${OPTIONS} web python ${PYTHON_ARGS} manage.py ${ARGS}

migrate:
	$(MAKE) manage ARGS="migrate ${ARGS}"

migrations:
	$(MAKE) manage ARGS="makemigrations ${ARGS}"

test:
	docker compose run --rm web pytest

lint:
	docker compose run --rm web ruff check .

ingest:
	docker compose run --rm web python manage.py ingest --sync ${ARGS}

explain:
	docker compose run --rm web python manage.py explain_queries
