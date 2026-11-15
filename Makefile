.PHONY: usage up down build logs shell manage migrate migrations test lint ingest

usage:
	@echo "Available commands:"
	@echo "up..............................Build and start everything (docker compose up --build)"
	@echo "down............................Stop everything (docker compose down)"
	@echo "build...........................Build the images"
	@echo "logs............................Follow the logs of all services"
	@echo "shell...........................Django shell in the running web container"
	@echo "manage..........................Any manage.py command, e.g. make manage ARGS=createsuperuser"
	@echo "migrate.........................Run Django migrations"
	@echo "migrations......................Create Django migrations"
	@echo "test............................Run the test suite (pytest)"
	@echo "lint............................Lint with ruff"
	@echo "ingest..........................Ingest from USGS now, e.g. make ingest ARGS=\"--updated-after 2026-01-01\""

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
