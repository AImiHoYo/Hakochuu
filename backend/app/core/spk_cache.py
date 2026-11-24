from dataclasses import dataclass


@dataclass(slots=True)
class SpeakerAssetEntry:
    character_id: str
    name: str | None = None
    description: str | None = None
    ref_speech_path: str | None = None
    ref_speech_text: str | None = None
    ref_sing_path: str | None = None
    lora_path: str | None = None
    embedding_ready: bool = False


class SpkEmbeddingCache:
    """活跃角色音色特征常驻内存缓存，避免每轮对话重复提取 Speaker Embedding。"""

    def __init__(self) -> None:
        self._cache: dict[str, SpeakerAssetEntry] = {}

    def get(self, character_id: str) -> SpeakerAssetEntry | None:
        return self._cache.get(character_id)

    def put(self, entry: SpeakerAssetEntry) -> None:
        self._cache[entry.character_id] = entry

    def invalidate(self, character_id: str) -> None:
        self._cache.pop(character_id, None)

    def size(self) -> int:
        return len(self._cache)
