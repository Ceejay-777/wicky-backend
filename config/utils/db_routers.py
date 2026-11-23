AI_LOG_DB = "ai_log"


class AILogRouter:
    """Send AIGenerationLog to its own connection so log writes autocommit
    outside the caller's transaction. In every real environment ``ai_log``
    points at the same database as ``default``; migrations run on ``default``.
    """

    def _is_log(self, model):
        return model._meta.label == "ai.AIGenerationLog"

    def db_for_read(self, model, **hints):
        return AI_LOG_DB if self._is_log(model) else None

    def db_for_write(self, model, **hints):
        return AI_LOG_DB if self._is_log(model) else None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == AI_LOG_DB:
            return app_label == "ai" and model_name == "aigenerationlog"
        return None
