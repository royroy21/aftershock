from rest_framework import exceptions
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """Every API error has one shape: {"error": {"code", "message", "details"}}.

    Without this, DRF's errors come in shapes a client would have to tell apart:
        {"non_field_errors": ["lat, lon and radius_km must be used together; ..."]}
        {"min_magnitude": ["A valid number is required."]}
        {"detail": "No Event matches the given query."}

    `code` is DRF's machine-readable code (e.g. "invalid", "not_found"); `details` holds
    per-field messages for validation errors and is null otherwise. Unhandled exceptions
    (500s) are left to Django.
    """
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, exceptions.ValidationError):
        error = {"code": "invalid", "message": "Invalid request.", "details": response.data}
    else:
        detail = response.data["detail"]
        error = {"code": detail.code, "message": str(detail), "details": None}
    response.data = {"error": error}
    return response
