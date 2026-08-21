from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/data-cleanup", tags=["data-cleanup"])


@router.get("/summary")
def get_data_cleanup_summary():
    raise HTTPException(
        status_code=410,
        detail="data-cleanup is deprecated. Legacy read-only cleanup diagnostics were removed from the UI.",
    )
