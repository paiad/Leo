from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

_DOTENV_CACHE: dict[str, str] | None = None
_DOTENV_LOCK = Lock()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_env_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("export "):
        text = text[7:].strip()
    if "=" not in text:
        return None
    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    return key, value


def _load_dotenv_cache() -> dict[str, str]:
    global _DOTENV_CACHE
    if _DOTENV_CACHE is not None:
        return _DOTENV_CACHE

    with _DOTENV_LOCK:
        if _DOTENV_CACHE is not None:
            return _DOTENV_CACHE

        env_map: dict[str, str] = {}
        dotenv_path = _project_root() / ".env"
        if dotenv_path.exists():
            for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
                parsed = _parse_env_line(raw_line)
                if parsed is None:
                    continue
                key, value = parsed
                env_map[key] = value
        _DOTENV_CACHE = env_map
        return env_map


def load_dotenv_into_environ(override: bool = False) -> dict[str, str]:
    env_map = _load_dotenv_cache()
    for key, value in env_map.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return env_map


def _read_windows_registry_env(name: str) -> str | None:
    try:
        import winreg  # type: ignore
    except Exception:
        return None

    paths = [
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ]
    for root, sub_key in paths:
        try:
            with winreg.OpenKey(root, sub_key) as key:
                value, _ = winreg.QueryValueEx(key, name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return None


def get_env(name: str, default: str | None = None) -> str | None:
    # 1) process env
    value = os.getenv(name)
    if value is not None and value.strip() != "":
        return value

    # 2) .env file
    dotenv_map = _load_dotenv_cache()
    value = dotenv_map.get(name)
    if value is not None and value.strip() != "":
        return value

    # 3) Windows user/system env (for unrefreshed terminals)
    if os.name == "nt":
        value = _read_windows_registry_env(name)
        if value is not None and value.strip() != "":
            return value

    return default

