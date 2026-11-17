from celery import shared_task


@shared_task(name="ai.delete_old_generation_logs")
def delete_old_generation_logs(batch_size: int = 1000) -> int:
    """Delete AIGenerationLog rows older than AI_LOG_RETENTION_DAYS, in batches."""
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from .models import AIGenerationLog

    cutoff = timezone.now() - timedelta(days=settings.AI_LOG_RETENTION_DAYS)
    deleted = 0
    while True:
        ids = list(
            AIGenerationLog.objects.filter(created_at__lt=cutoff)
            .order_by("id")
            .values_list("id", flat=True)[:batch_size]
        )
        if not ids:
            return deleted
        deleted += AIGenerationLog.objects.filter(id__in=ids).delete()[0]
