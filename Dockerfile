FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# GeoDjango needs GDAL, GEOS and PROJ at runtime; gdal-bin pulls in all three.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gdal-bin \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.13.7

WORKDIR /app

# Install dependencies before copying source so code changes don't bust this layer.
# Dev dependencies are included so the test suite can run inside the container.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

EXPOSE 8000
# Dev server by design for this project; see README "Before production".
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
