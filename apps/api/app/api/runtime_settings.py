from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db.session import get_session
from app.schemas.runtime_settings import RuntimeSettingRead, RuntimeSettingUpdate
from app.services import conversation_service, runtime_settings_service

router = APIRouter(prefix="/runtime-settings", tags=["runtime-settings"])


@router.get("/default", response_model=RuntimeSettingRead)
def get_default_runtime_setting(session: Session = Depends(get_session)):
    setting = runtime_settings_service.get_global_setting(session)
    return runtime_settings_service.serialize_setting(setting, scope="default", session=session)


@router.patch("/default", response_model=RuntimeSettingRead)
def update_default_runtime_setting(payload: RuntimeSettingUpdate, session: Session = Depends(get_session)):
    try:
        setting = runtime_settings_service.update_global_setting(session, payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return runtime_settings_service.serialize_setting(setting, scope="default", session=session)


@router.get("/conversations/{conversation_id}", response_model=RuntimeSettingRead)
def get_conversation_runtime_setting(conversation_id: str, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    setting = runtime_settings_service.get_effective_setting(session, conversation_id)
    return runtime_settings_service.serialize_setting(setting, scope="conversation", session=session)


@router.patch("/conversations/{conversation_id}", response_model=RuntimeSettingRead)
def update_conversation_runtime_setting(conversation_id: str, payload: RuntimeSettingUpdate, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        setting = runtime_settings_service.update_conversation_setting(session, conversation_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return runtime_settings_service.serialize_setting(setting, scope="conversation", session=session)
