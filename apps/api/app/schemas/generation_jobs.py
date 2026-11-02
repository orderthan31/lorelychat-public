from pydantic import BaseModel, Field
from app.schemas.conversations import MessageGenerationJobRead


class GenerationJobRetryRequest(BaseModel):
    expected_state_version: int = Field(ge=0, strict=True)


class ActiveGenerationJobsRead(BaseModel):
    jobs: list[MessageGenerationJobRead]
