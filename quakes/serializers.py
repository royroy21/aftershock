from rest_framework import serializers

from quakes.models import Event

MAX_RADIUS_KM = 2000
RADIUS_PARAMS = ("lat", "lon", "radius_km")


class EventSerializer(serializers.ModelSerializer):
    # Plain latitude/longitude rather than GeoJSON: simpler for most API clients.
    latitude = serializers.FloatField(source="location.y", read_only=True)
    longitude = serializers.FloatField(source="location.x", read_only=True)
    distance_km = serializers.SerializerMethodField(
        help_text="Distance from the radius-search centre; null when not searching by radius."
    )

    class Meta:
        model = Event
        fields = [
            "id",
            "time",
            "magnitude",
            "magnitude_type",
            "latitude",
            "longitude",
            "depth_km",
            "place",
            "title",
            "url",
            "event_type",
            "status",
            "network",
            "significance",
            "tsunami",
            "felt",
            "alert",
            "source_updated",
            "distance_km",
        ]
        read_only_fields = fields

    def get_distance_km(self, event) -> float | None:
        distance = getattr(event, "distance", None)  # annotated only by a radius search
        return round(distance.km, 3) if distance is not None else None


class EventQuery(serializers.Serializer):
    """Query parameters for the event list. Validates them; the view applies them."""

    start = serializers.DateTimeField(
        required=False, help_text="Origin time on or after (ISO 8601)."
    )
    end = serializers.DateTimeField(
        required=False, help_text="Origin time on or before (ISO 8601)."
    )
    min_magnitude = serializers.FloatField(required=False)
    max_magnitude = serializers.FloatField(required=False)
    lat = serializers.FloatField(
        required=False, min_value=-90, max_value=90, help_text="Radius search centre latitude."
    )
    lon = serializers.FloatField(
        required=False, min_value=-180, max_value=180, help_text="Radius search centre longitude."
    )
    radius_km = serializers.FloatField(
        required=False,
        min_value=0,
        max_value=MAX_RADIUS_KM,
        help_text=f"Events within this many km of lat/lon (max {MAX_RADIUS_KM}).",
    )

    def validate(self, data):
        given = [name for name in RADIUS_PARAMS if name in data]
        if given and len(given) != len(RADIUS_PARAMS):
            missing = [name for name in RADIUS_PARAMS if name not in given]
            raise serializers.ValidationError(
                f"lat, lon and radius_km must be used together; missing: {', '.join(missing)}."
            )
        return data
