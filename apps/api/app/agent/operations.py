"""One strict tool registry shared by LangChain and official MCP adapters."""
from dataclasses import dataclass
from pydantic import BaseModel
from app.agent.contracts import TOOL_SCHEMAS, AgentError, CHARACTER_FIELDS, WORLD_FIELDS
from app.agent.knowledge import product_help, stage_help

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    required_scope: str
    read_only: bool
    external_effect: bool

_DRAFT={'lore_stage_character','lore_stage_world','lore_revise_draft','lore_preview_draft','lore_request_approval','lore_stage_image_registration'}
_COMMIT={'lore_commit_draft','lore_generate_image'}
_IMAGE={'lore_plan_image','lore_get_image_job'}
_READ={'lore_image_context','lore_capabilities','lore_search','lore_get_character','lore_get_world','lore_navigation_target','lore_preview_draft','lore_get_operation','lore_get_image_job'}
_DESCRIPTIONS={
 'lore_image_context':'Inspect existing image candidates and reference capability; thumbnails appear in the session. For an image creation request, next call lore_plan_image to display the unapproved plan and owner reference-choice controls. No choice is needed to stage that plan. This read does not choose, approve or generate images.',
 'lore_capabilities':'Read bounded Lorechat product/workflow help, allowed field meanings/types, tools and limits. Use when needed, not a mandatory first step or substitute for staging a requested creation. Contains no library content.',
 'lore_search':'Resolve existing character/world targets by bounded name search (query, kind, limit <=20). Returns ids and labels, not full content. Read relevant fields next; ask if target identity is ambiguous, never choose the first match arbitrarily.',
 'lore_get_character':'Read task-relevant authored character fields on demand. Obey optional explicit user context restrictions.',
 'lore_get_world':'Read task-relevant authored world fields on demand. Obey optional explicit user context restrictions.',
 'lore_navigation_target':'Return a validated application navigation path; never navigates or fetches URLs.',
 'lore_stage_character':stage_help('character'),
 'lore_stage_world':stage_help('world'),
 'lore_revise_draft':'Revise an existing draft, not the live resource: use returned draft_id, current expected_version and allowed fields. Invalidates earlier approval. Inspect the new normalized preview and request owner review again.',
 'lore_preview_draft':'Read current draft by draft_id: normalized before/after, changes, status, version and preview_hash. Use before review or to reconcile stale state; a draft is not a saved resource.',
 'lore_request_approval':'Request human review of the staged draft_id after inspecting its normalized preview. For a new draft this requests review, NOT owner approval or saving. Read the returned status; this tool never grants approval. Tell the user what changed and any assumptions; stop for owner review.',
 'lore_commit_draft':'Commit only an exactly owner-approved preview with a unique request id. Reuse that id to reconcile; never invent approval.',
 'lore_get_operation':'Read a durable operation receipt by request id without reapplying the operation.',
 'lore_plan_image':'Stage an UNAPPROVED character or world image plan so the UI can show reference thumbnails and owner choice controls. Call before asking the owner to choose a reference in the UI, even when the generation mode is undecided. Staging costs nothing and does not grant consent; paid dispatch still requires exact owner approval. World scenes can become thumbnails, not chat backgrounds.',
 'lore_generate_image':'Execute an exactly owner-approved image plan once. Generation does not register assets or change avatars or world thumbnails.',
 'lore_get_image_job':'Read image job status and retained output identifiers.',
 'lore_stage_image_registration':'Stage retained outputs as non-default character assets, or exactly one output as a world thumbnail (empty metadata). Must match plan target kind; requires separate owner approval.'
}
TOOL_SPECS={name:ToolSpec(name,_DESCRIPTIONS[name],schema,
    'lore:commit' if name in _COMMIT else 'lore:image:generate' if name in _IMAGE else 'lore:draft' if name in _DRAFT else 'lore:read',
    name in _READ,name in _COMMIT) for name,schema in TOOL_SCHEMAS.items()}

class AgentOperations:
    def __init__(self, service, principal, session_id=None, request_id=None):
        self.service=service
        self.principal=principal
        self.session_id=session_id
        self.request_id=request_id
        self._context_targets=set()

    def _session_fields(self,kind,id):
        allowed=self.service.session_fields(self.principal,self.session_id,kind,id)
        target=(kind,id)
        if target not in self._context_targets and len(self._context_targets)>=8:
            raise AgentError('Context target limit reached for this turn',422)
        self._context_targets.add(target)
        return allowed

    @staticmethod
    def tool_specs():
        return dict(TOOL_SPECS)

    async def execute(self,name,arguments):
        spec=TOOL_SPECS.get(name)
        if spec is None: raise AgentError('Unknown operation',404)
        args=spec.args_schema.model_validate(arguments).model_dump()
        s,p=self.service,self.principal
        s.require_enabled(p,spec.required_scope)
        if self.session_id and p.is_owner:
            if name in ('lore_get_character','lore_get_world'):
                kind=name.removeprefix('lore_get_')
                allowed=self._session_fields(kind,args['id'])
                if args['fields'] is None: args['fields']=sorted(allowed)
                if not set(args['fields']).issubset(allowed): raise AgentError('Context field was not selected',403)
            if name in ('lore_stage_character','lore_stage_world') and args['target_id']:
                allowed=self._session_fields(name.removeprefix('lore_stage_'),args['target_id'])
                if not set(args['fields']).issubset(allowed): raise AgentError('Context field was not selected',403)
            if 'draft_id' in args:
                draft=s.get_draft(args['draft_id'],p)
                if draft['session_id']!=self.session_id: raise AgentError('Draft belongs to another session',403)
                if name=='lore_revise_draft' and draft['target_id'] and draft['kind'] in ('character','world'):
                    allowed=self._session_fields(draft['kind'],draft['target_id'])
                    if not set(args['fields']).issubset(allowed): raise AgentError('Context field was not selected',403)
        if name=='lore_capabilities':
            return {'tools':sorted(TOOL_SCHEMAS),'character_fields':sorted(CHARACTER_FIELDS),'world_fields':sorted(WORLD_FIELDS),'approval':'owner_only','images_require_independent_configuration':True,'limits':{'search':20,'context':8,'images':4}, **product_help()}
        if name=='lore_search': return s.search(p,**args)
        if name in ('lore_get_character','lore_get_world'): return s.get_entity(p,name.removeprefix('lore_get_'),**args)
        if name=='lore_navigation_target':
            kind,id=args['kind'],args['id']
            if kind in ('character','world'):
                if not id: raise AgentError('Entity id required',422)
                s.get_entity(p,kind,id,fields=[])
                path=('/characters/' if kind=='character' else '/world-settings/')+id
            else:
                if id: raise AgentError('This navigation target takes no id',422)
                path={'home':'/','characters':'/characters','worlds':'/world-settings','settings':'/settings/lore-agent'}[kind]
            return {'path':path}
        if name in ('lore_stage_character','lore_stage_world'):
            return s.create_draft(p,name.removeprefix('lore_stage_'),session_id=self.session_id,**args)
        if name=='lore_revise_draft':
            return s.revise_draft(p,args['draft_id'],args['expected_version'],args['fields'])
        if name=='lore_preview_draft': return s.get_draft(args['draft_id'],p)
        if name=='lore_request_approval': return s.request_approval(p,args['draft_id'])
        if name in ('lore_commit_draft','lore_generate_image'):
            if name=='lore_generate_image' and s.get_draft(args['draft_id'],p)['kind']!='image_generation': raise AgentError('Expected image generation draft',422)
            return await s.commit_draft(p,**args)
        if name=='lore_get_operation': return s.get_operation(args['request_id'],p)
        if name=='lore_image_context': return s.image_context(p,session_id=self.session_id,request_id=self.request_id,**args)
        if name=='lore_plan_image': return s.plan_image(p,session_id=self.session_id,**args)
        if name=='lore_get_image_job': return s.get_image_job(args['job_id'],p)
        if name=='lore_stage_image_registration': return s.stage_image_registration(p,session_id=self.session_id,**args)
        raise AgentError('Unknown operation',404)
