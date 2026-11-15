from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.pagination import CursorPagination

from quakes.models import Event
from quakes.serializers import EventQuery, EventSerializer


class EventCursorPagination(CursorPagination):
    """Newest first. Cursor (keyset) paging: no COUNT(*), and deep pages cost the same as the
    first. Stable while new events are inserted, unlike offset paging."""

    ordering = ("-time", "-id")
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


@extend_schema_view(list=extend_schema(parameters=[EventQuery]))
class EventViewSet(viewsets.ReadOnlyModelViewSet):
    """Earthquakes and other seismic events from USGS. Deleted and merged events are hidden."""

    serializer_class = EventSerializer
    pagination_class = EventCursorPagination

    def get_queryset(self):
        events = Event.objects.active()
        if self.action != "list":
            return events  # filters only apply to the list, never to a detail lookup

        query = EventQuery(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data

        if "start" in params:
            events = events.filter(time__gte=params["start"])
        if "end" in params:
            events = events.filter(time__lte=params["end"])
        if "min_magnitude" in params:
            events = events.filter(magnitude__gte=params["min_magnitude"])
        if "max_magnitude" in params:
            events = events.filter(magnitude__lte=params["max_magnitude"])
        if "radius_km" in params:  # EventQuery guarantees lat and lon come with it
            centre = Point(params["lon"], params["lat"], srid=4326)
            # `location` is a geography column, so this is ST_DWithin in metres on the
            # spheroid: correct across the antimeridian and near the poles, and it uses the
            # GiST index.
            events = events.filter(location__dwithin=(centre, D(km=params["radius_km"])))
            events = events.annotate(distance=Distance("location", centre))
        return events
