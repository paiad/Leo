from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.config import config
from bff.domain.models import WorkspaceModelCreate, WorkspaceModelUpdate
from bff.repositories.model_store import ModelSqliteStore


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelService:
    def __init__(self, store: ModelSqliteStore):
        self._store = store
        self._ensure_seed_model()

    @staticmethod
    def _default_model_payload() -> dict[str, Any]:
        default_llm = config.llm.get("default")
        model_name = default_llm.model if default_llm else "unknown"
        provider = "openai-compatible"
        base_url = default_llm.base_url if default_llm else ""
        ts = now_iso()
        return {
            "id": str(uuid4()),
            "name": model_name,
            "provider": provider,
            "baseUrl": base_url,
            "apiKey": default_llm.api_key if default_llm else "",
            "enabled": True,
            "createdAt": ts,
            "updatedAt": ts,
        }

    def _ensure_seed_model(self) -> None:
        models = self._store.list_models()
        if models:
            active_id = self._store.get_active_model_id()
            if active_id and self._store.get_model(active_id):
                return
            self._store.set_active_model_id(models[0]["id"])
            return

        seed = self._default_model_payload()
        self._store.create_model(seed)
        self._store.set_active_model_id(seed["id"])

    def list_models(self) -> list[dict[str, Any]]:
        return self._store.list_models()

    def create_model(self, payload: WorkspaceModelCreate) -> dict[str, Any]:
        ts = now_iso()
        model = {
            "id": str(uuid4()),
            "name": payload.name.strip(),
            "provider": payload.provider.strip(),
            "baseUrl": payload.baseUrl.strip(),
            "apiKey": payload.apiKey.strip(),
            "enabled": payload.enabled,
            "createdAt": ts,
            "updatedAt": ts,
        }
        self._store.create_model(model)
        if not self._store.get_active_model_id():
            self._store.set_active_model_id(model["id"])
        return model

    def update_model(self, model_id: str, payload: WorkspaceModelUpdate) -> dict[str, Any] | None:
        current = self._store.get_model(model_id)
        if current is None:
            return None

        updated_payload = {
            "name": payload.name.strip(),
            "provider": payload.provider.strip(),
            "baseUrl": payload.baseUrl.strip(),
            "apiKey": payload.apiKey.strip(),
            "enabled": payload.enabled,
            "updatedAt": now_iso(),
        }
        return self._store.update_model(model_id, updated_payload)

    def delete_model(self, model_id: str) -> bool:
        was_active = self._store.get_active_model_id() == model_id
        deleted = self._store.delete_model(model_id)
        if not deleted:
            return False
        if was_active:
            models = self._store.list_models()
            self._store.set_active_model_id(models[0]["id"] if models else None)
        return True

    def get_active_model(self) -> dict[str, Any] | None:
        active_id = self._store.get_active_model_id()
        if not active_id:
            return None
        return self._store.get_model(active_id)

    def set_active_model(self, model_id: str) -> dict[str, Any] | None:
        model = self._store.get_model(model_id)
        if model is None:
            return None
        self._store.set_active_model_id(model_id)
        return model

    def get_runtime_model_name(self) -> str:
        active = self.get_active_model()
        if active and active.get("name"):
            return str(active["name"])
        default_llm = config.llm.get("default")
        return default_llm.model if default_llm else "unknown"

    def chat_model_config(self) -> dict[str, Any]:
        models = self.list_models()
        active = self.get_active_model()
        active_name = self.get_runtime_model_name()
        default_llm = config.llm.get("default")
        default_base_url = ""
        if active and isinstance(active.get("baseUrl"), str):
            default_base_url = active["baseUrl"]
        elif default_llm:
            default_base_url = default_llm.base_url
        provider = active.get("provider") if active else "openai-compatible"
        return {
            "provider": provider,
            "defaultBaseUrl": default_base_url,
            "defaultModel": active_name,
            "availableModels": [item["name"] for item in models if item.get("name")],
            "activeModelId": active["id"] if active else None,
            "activeApiKey": active.get("apiKey") if active else "",
        }
