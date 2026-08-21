from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db.session import get_session
from app.schemas.chat_commands import ChatCommandCreate, ChatCommandRead, ChatCommandUpdate
from app.schemas.pagination import ChatCommandPageRead
from app.services import chat_command_service

router = APIRouter(prefix="/chat-commands", tags=["chat-commands"])


@router.get("", response_model=list[ChatCommandRead] | ChatCommandPageRead)
def list_chat_commands(
    include_disabled: bool = Query(False),
    paginated: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    session: Session = Depends(get_session),
):
    chat_command_service.seed_default_chat_commands(session)
    if paginated:
        all_items = chat_command_service.list_chat_commands(session, include_disabled=include_disabled)
        search = (q or "").strip()
        if search:
            lowered = search.lower()
            all_items = [
                command for command in all_items
                if lowered in f"{command.name or ''} {command.display_name or ''} {command.description or ''} {command.generation_prompt or ''} {command.postprocess_prompt or ''} {command.prompt or ''}".lower()
            ]
        total = len(all_items)
        pages = max(1, (total + page_size - 1) // page_size)
        safe_page = min(page, pages)
        start = (safe_page - 1) * page_size
        return ChatCommandPageRead(items=[ChatCommandRead.model_validate(item.model_dump()) for item in all_items[start:start + page_size]], total=total, page=safe_page, page_size=page_size, pages=pages)
    return chat_command_service.list_chat_commands(session, include_disabled=include_disabled)


@router.post("", response_model=ChatCommandRead)
def create_chat_command(payload: ChatCommandCreate, session: Session = Depends(get_session)):
    try:
        return chat_command_service.create_chat_command(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{command_id}", response_model=ChatCommandRead)
def get_chat_command(command_id: str, session: Session = Depends(get_session)):
    command = chat_command_service.get_chat_command(session, command_id)
    if not command:
        raise HTTPException(status_code=404, detail="Chat command not found")
    return command


@router.patch("/{command_id}", response_model=ChatCommandRead)
def update_chat_command(command_id: str, payload: ChatCommandUpdate, session: Session = Depends(get_session)):
    command = chat_command_service.get_chat_command(session, command_id)
    if not command:
        raise HTTPException(status_code=404, detail="Chat command not found")
    try:
        return chat_command_service.update_chat_command(session, command, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{command_id}", status_code=204)
def delete_chat_command(command_id: str, session: Session = Depends(get_session)):
    command = chat_command_service.get_chat_command(session, command_id)
    if not command:
        raise HTTPException(status_code=404, detail="Chat command not found")
    chat_command_service.delete_chat_command(session, command)
    return None
