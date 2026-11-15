"""Internal controls use Lorechat's existing trusted single-user app boundary.

External MCP retains its own scoped bearer authentication. Provider credentials
remain server-side and are resolved by the selected model/account, not here.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError
from app.agent.contracts import (AgentError, SettingsUpdate, RequestInput, MessageInput,
    ReviseBody, ApprovalBody, CommitBody, ClientInput, RegistrationBody, NewImageChoiceInput,
    AgentSettingsView, AgentSessionView, AgentDraftView, OperationView, ClientView, ImageJobView,
    Principal, SCOPES)
from app.agent.service import AgentService

class AgentRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handle(request):
            try:
                return await original(request)
            except AgentError as exc:
                raise HTTPException(exc.status_code,str(exc)) from None
            except ValidationError:
                # Do not reflect provider values or full input payloads.
                raise HTTPException(422,'Invalid Lore Agent input') from None
        return handle

router=APIRouter(prefix='/lore-agent',tags=['lore-agent'],route_class=AgentRoute)

def get_agent_service(request: Request):
    service=getattr(request.app.state,'agent_service',None)
    if service is not None: return service
    engine=getattr(request.app.state,'engine',None)
    if engine is None: raise HTTPException(503,'Lore Agent storage is not initialized')
    return AgentService(engine)

def owner(request: Request, authorization: str | None=Header(default=None)):
    # This is the same private app access boundary as existing resource editors,
    # not a second login. Never interpret an external MCP/provider bearer as UI
    # approval authority. The MCP transport constructs its own non-owner actor.
    if authorization is not None:
        raise HTTPException(403, 'Use the scoped MCP endpoint for bearer access')
    origin = request.headers.get('origin')
    if origin is not None:
        from app.core.config import get_settings
        allowed = {value.strip() for value in get_settings().cors_origins.split(',') if value.strip()}
        allowed.add(f'{request.url.scheme}://{request.url.netloc}')
        if origin not in allowed:
            raise HTTPException(403, 'Untrusted application origin')
    return Principal('owner', SCOPES, True)

@router.post('/tools/{name}')
async def owner_tool(name: str, arguments: dict, principal=Depends(owner), service=Depends(get_agent_service)):
    from app.agent.operations import AgentOperations
    # Registry validation rejects unknown tools and extra arguments; no raw HTTP.
    return await AgentOperations(service,principal).execute(name,arguments)

@router.get('/settings',response_model=AgentSettingsView)
def settings(service=Depends(get_agent_service)):
    return service.settings_view()

@router.put('/settings',response_model=AgentSettingsView)
def update_settings(payload: SettingsUpdate, principal=Depends(owner), service=Depends(get_agent_service)):
    service.update_settings(payload)
    return service.settings_view()

@router.get('/sessions',response_model=list[AgentSessionView])
def sessions(principal=Depends(owner),service=Depends(get_agent_service)):
    return service.list_sessions(principal)

@router.post('/sessions',response_model=AgentSessionView)
def create_session(payload: RequestInput,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.create_session(principal,payload.request_id)

@router.get('/sessions/{id}',response_model=AgentSessionView)
def session(id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.get_session(id,principal)

@router.post('/sessions/{id}/messages',response_model=AgentSessionView)
async def message(id: str,payload: MessageInput,principal=Depends(owner),service=Depends(get_agent_service)):
    from app.agent.runtime import run_agent_turn
    return await run_agent_turn(service,principal,id,payload.request_id,payload.content,[x.model_dump() for x in payload.context])

@router.get('/drafts/{id}',response_model=AgentDraftView)
def draft(id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.get_draft(id,principal)

@router.post('/drafts/{id}/revise',response_model=AgentDraftView)
def revise(id: str,payload: ReviseBody,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.revise_draft(principal,id,payload.expected_version,payload.fields)

@router.post('/drafts/{id}/approve',response_model=AgentDraftView)
def approve(id: str,payload: ApprovalBody,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.approve_draft(principal,id,payload.expected_version,payload.preview_hash)

@router.post('/drafts/{id}/commit',response_model=OperationView)
async def commit(id: str,payload: CommitBody,principal=Depends(owner),service=Depends(get_agent_service)):
    return await service.commit_draft(principal,id,payload.request_id,payload.expected_version,payload.preview_hash)

@router.get('/operations/{request_id}',response_model=OperationView)
def operation(request_id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.get_operation(request_id,principal)

@router.get('/mcp/clients',response_model=list[ClientView])
def clients(principal=Depends(owner),service=Depends(get_agent_service)):
    return service.list_clients()

@router.post('/mcp/clients')
def create_client(payload: ClientInput,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.create_client(payload.name,payload.scopes)

@router.delete('/mcp/clients/{id}',status_code=204)
def revoke_client(id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    service.revoke_client(id)
    return Response(status_code=204)

@router.get('/image-context/{target_kind}/{target_id}')
def image_context(target_kind: str,target_id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.image_context(principal,target_id,target_kind)

@router.get('/image-context/{target_kind}/{target_id}/references/{reference_id}')
def reference_preview(target_kind: str,target_id: str,reference_id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    image=service.image_reference_preview(principal,target_kind,target_id,reference_id)
    return Response(content=image['data'],media_type=image['mime_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@router.post('/drafts/{draft_id}/image-choice',response_model=AgentDraftView)
def image_choice(draft_id: str,payload: NewImageChoiceInput,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.choose_image_mode(principal,draft_id,**payload.model_dump())

@router.get('/drafts/{draft_id}/reference')
def draft_reference(draft_id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    image=service.draft_image_reference(principal,draft_id)
    return Response(content=image['data'],media_type=image['mime_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@router.get('/image-jobs/{id}',response_model=ImageJobView)
def image_job(id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.get_image_job(id,principal)

@router.post('/image-jobs/{id}/registration',response_model=AgentDraftView)
def registration(id: str,payload: RegistrationBody,principal=Depends(owner),service=Depends(get_agent_service)):
    return service.stage_image_registration(principal,id,**payload.model_dump())

@router.get('/image-outputs/{id}')
def image_output(id: str,principal=Depends(owner),service=Depends(get_agent_service)):
    output=service.image_output(id,principal)
    return Response(content=output['data'],media_type=output['mime_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
