from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/relationship-map", tags=["relationship-map"])


@router.get("")
def get_relationship_map():
    raise HTTPException(
        status_code=410,
        detail="relationship-map is deprecated. Use the conversation drawer relationship tab and /conversations/{conversation_id}/context instead.",
    )
