from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from quakes.models import Event


class ReadOnlyAdminMixin:
    """Rows mirror USGS and would be overwritten by the next ingest, so the admin only views."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Event)
class EventAdmin(ReadOnlyAdminMixin, GISModelAdmin):
    list_display = ["id", "time", "magnitude", "magnitude_type", "place", "status", "event_type"]
    # Only filters that need no query: free-text columns would run SELECT DISTINCT on a big table.
    list_filter = ["status", "tsunami"]
    search_fields = ["id", "place"]
    ordering = ["-time", "-id"]
    # Skip the extra COUNT(*) over the whole table on every list page; it gets slow as it grows.
    show_full_result_count = False
