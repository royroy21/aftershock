import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aftershock.settings")

app = Celery("aftershock")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
