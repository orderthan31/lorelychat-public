from pydantic import BaseModel, Field


class TTSVoiceOption(BaseModel):
    key: str
    label: str
    gender: str
    provider: str = "supertonic"


class TTSModelOption(BaseModel):
    key: str
    label: str
    provider: str
    cost_hint: str | None = None


class TTSSampleRequest(BaseModel):
    text: str | None = Field(default=None, max_length=500)
    voice_style: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None


class TTSGenerateResponse(BaseModel):
    audio_url: str
    tts_text: str
    voice_style: str
    tts_provider: str = "supertonic"
    tts_model: str = "supertonic-3"
