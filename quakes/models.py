from django.contrib.gis.db import models


class EventQuerySet(models.QuerySet):
    def active(self):
        """Events USGS has not deleted or merged away. Use this for anything user-facing."""
        return self.exclude(status__in=[Event.Status.DELETED, Event.Status.MERGED])


class Event(models.Model):
    """A seismic event as last reported by USGS.

    Rows mirror USGS's *current* view of each event: a newer upstream version (decided by
    `source_updated`) overwrites the row. Deletions are just another version: USGS sends the
    event with status "deleted", which we store rather than removing the row, so deletion uses
    the same write path and leaves an audit trail. Likewise, an id USGS has merged into another
    event is marked "merged" (our status, not USGS's) so the quake isn't counted twice.

    The default manager deliberately does not hide deleted or merged rows: ingest must see them
    when comparing `source_updated`, or an older copy could overwrite a deletion or merge.
    User-facing code filters explicitly with `Event.objects.active()`.
    """

    class Status(models.TextChoices):
        AUTOMATIC = "automatic"
        REVIEWED = "reviewed"
        DELETED = "deleted"
        MERGED = "merged"

    id = models.CharField(primary_key=True, max_length=64, help_text="USGS event id.")
    time = models.DateTimeField(db_index=True, help_text="Origin time (UTC).")
    source_updated = models.DateTimeField(
        db_index=True,  # MAX(source_updated) is each ingest's start point
        help_text="When USGS last updated this event. A newer value overwrites the row.",
    )
    status = models.CharField(max_length=16, choices=Status.choices)

    # Indexed so a rare magnitude filter (e.g. min_magnitude=9) doesn't scan the whole table;
    # see QUERY_PLANS.md.
    magnitude = models.FloatField(null=True, blank=True, db_index=True)
    magnitude_type = models.CharField(max_length=16, blank=True)
    location = models.PointField(geography=True, srid=4326)
    depth_km = models.FloatField(null=True, blank=True)

    place = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=500, blank=True)

    event_type = models.CharField(
        max_length=64, blank=True, help_text="e.g. earthquake, quarry blast."
    )
    network = models.CharField(max_length=16, blank=True)
    significance = models.IntegerField(null=True, blank=True)
    tsunami = models.BooleanField(default=False)
    felt = models.IntegerField(
        null=True, blank=True, help_text="Number of 'Did You Feel It?' reports."
    )
    alert = models.CharField(max_length=16, blank=True, help_text="PAGER alert level.")

    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["-time", "-id"]

    def __str__(self):
        return self.title or self.id
