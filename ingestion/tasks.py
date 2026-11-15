"""Celery entry point for ingest. A thin wrapper: all logic lives in ingestion.ingest."""

import logging
from datetime import datetime

from celery import shared_task

from ingestion.client import USGSError
from ingestion.ingest import IngestAlreadyRunning, ingest

logger = logging.getLogger(__name__)


@shared_task(
    # The client already retries brief blips within seconds; this covers longer USGS outages.
    autoretry_for=(USGSError,),
    retry_backoff=60,
    retry_backoff_max=900,
    retry_jitter=True,
    max_retries=5,
)
def ingest_task(updated_after: str | None = None) -> None:
    """Run one ingest. `updated_after` is an ISO 8601 string because task args must be JSON.

    A failed attempt wrote nothing (the run is one transaction), so a retry simply starts again
    from the same point.
    """
    start = datetime.fromisoformat(updated_after) if updated_after else None
    try:
        ingest(updated_after=start)
    except IngestAlreadyRunning:
        # Not an error: a previous run (e.g. last cron tick) is still going.
        logger.info("Skipping ingest: another run is in progress")
