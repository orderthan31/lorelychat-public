from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session
from app.db.models import ChatCommand
from app.ops.catalog_queries import catalog_page

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
    q: str | None = Query(None, max_length=512),
    session: Session = Depends(get_session),
):
    chat_command_service.seed_default_chat_commands(session)
    if paginated:
        result = catalog_page(session, ChatCommand, fields=[ChatCommand.name, ChatCommand.display_name, ChatCommand.description, ChatCommand.generation_prompt, ChatCommand.postprocess_prompt, ChatCommand.prompt], order=[ChatCommand.priority.desc(), ChatCommand.name], page=page, page_size=page_size, q=q, predicates=() if include_disabled else (ChatCommand.enabled == True,))
        result["items"] = [ChatCommandRead.model_validate(item.model_dump()) for item in result["items"]]
        return ChatCommandPageRead(**result)
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
