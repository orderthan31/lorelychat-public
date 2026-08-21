from pydantic import BaseModel, Field


class PageMeta(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int


class CharacterPageRead(PageMeta):
    items: list["CharacterRead"] = Field(default_factory=list)


class WorldSettingPageRead(PageMeta):
    items: list["WorldSettingRead"] = Field(default_factory=list)


class ChatCommandPageRead(PageMeta):
    items: list["ChatCommandRead"] = Field(default_factory=list)


from app.schemas.characters import CharacterRead  # noqa: E402
from app.schemas.world_settings import WorldSettingRead  # noqa: E402
from app.schemas.chat_commands import ChatCommandRead  # noqa: E402

CharacterPageRead.model_rebuild()
WorldSettingPageRead.model_rebuild()
ChatCommandPageRead.model_rebuild()
