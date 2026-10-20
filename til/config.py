import base64
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import win32crypt


FORMATS = ("OpenAI Chat Completions", "OpenAI Responses", "Anthropic Messages")
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "TIL"
CONFIG_PATH = CONFIG_DIR / "settings.json"
LEGACY_CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "TextLens"


@dataclass
class Settings:
    provider: str = "自定义供应商"
    api_format: str = FORMATS[0]
    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    auto_capture: bool = False
    copy_fallback: bool = False
    max_chars: int = 2000
    timeout: int = 45

    def validate(self):
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("请填写供应商名称和模型 ID。")
        if self.api_format not in FORMATS:
            raise ValueError("不支持的上游格式。")
        parsed = urlsplit(self.endpoint.strip())
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("请填写有效的 API 请求地址，不要在地址中嵌入账号、密钥或查询参数。")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("远程 API 必须使用 HTTPS；仅本机地址允许 HTTP。")
        if not 20 <= self.max_chars <= 10000:
            raise ValueError("划词长度上限须在 20–10000 字之间。")
        if not 10 <= self.timeout <= 180:
            raise ValueError("超时须在 10–180 秒之间。")
        if any(char in self.api_key for char in "\r\n"):
            raise ValueError("API Key 不能包含换行。")


def load_settings(path=None):
    path = CONFIG_PATH if path is None else path
    # 改名前的配置在 %APPDATA%\TextLens 下；新目录还没有配置时沿用旧文件，用户不必重填。
    if not path.exists() and path == CONFIG_PATH:
        legacy = LEGACY_CONFIG_DIR / "settings.json"
        if legacy.exists():
            path = legacy
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    secret = data.pop("encrypted_api_key", "")
    data.pop("api_key", None)
    settings = Settings(**{key: value for key, value in data.items() if key in Settings.__dataclass_fields__})
    if secret:
        settings.api_key = win32crypt.CryptUnprotectData(base64.b64decode(secret, validate=True), None, None, None, 1)[1].decode("utf-8")
    settings.validate()
    return settings


def save_settings(settings, path=None):
    path = CONFIG_PATH if path is None else path
    settings.validate()
    data = asdict(settings)
    secret = data.pop("api_key")
    data["encrypted_api_key"] = base64.b64encode(win32crypt.CryptProtectData(secret.encode("utf-8"), "TIL API Key", None, None, None, 1)).decode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
