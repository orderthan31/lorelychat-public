"""Transaction-owned shared domain operations; no provider or HTTP calls here."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from uuid import uuid4

from sqlalchemy import select, text
from sqlmodel import Session
from app.agent import models as m
from app.agent.contracts import (AgentError, Principal, SCOPES, SettingsUpdate, ClientInput,
    CHARACTER_FIELDS, WORLD_FIELDS, ContextSelection, MessageInput, ImagePlanInput, RegistrationBody)
from app.db.models import Character, WorldSetting, CharacterAsset, utc_now
from app.schemas.characters import CharacterCreate, CharacterUpdate, DEFAULT_TRAIT_SCORES, DEFAULT_FORBIDDEN_RULES
from app.schemas.world_settings import WorldSettingCreate, WorldSettingUpdate


def uid(prefix):
    return prefix + '_' + uuid4().hex

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def bounded(value, maximum=64000):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > maximum:
        raise AgentError('Input exceeds size budget', 422)
    return value

def public(data):
    return {k: deepcopy(v) for k, v in data.items() if not k.startswith('_')}

class AgentService:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def _write(self):
        # Serialize SQLite writers before any read: one transaction owns CAS,
        # entity changes, approval consumption and request receipt.
        with self.engine.connect() as c:
            c.exec_driver_sql('BEGIN IMMEDIATE')
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def _get(self, c, table, id, principal=None):
        row = c.execute(select(table).where(table.c.id == id)).mappings().first()
        if row is None:
            raise AgentError('Not found', 404)
        if principal is not None and not principal.is_owner and row['owner_id'] != principal.id:
            raise AgentError('Not found', 404)
        return deepcopy(row['data'])

    def _put(self, c, table, id, data):
        c.execute(table.update().where(table.c.id == id).values(data=deepcopy(data)))

    @property
    def checkpoint_path(self):
        from pathlib import Path
        from app.core.config import get_settings
        db=self.engine.url.database
        if not db or db==':memory:': raise AgentError('Persistent Agent checkpoints require a file-backed database',503)
        path=Path(db).resolve().parent/'lore-agent-private'/'checkpoints.sqlite'
        uploads=Path(get_settings().upload_root).resolve()
        if path.is_relative_to(uploads): raise AgentError('Private checkpoints cannot be stored in uploads',503)
        return path

    def resolve_llm_route(self,principal):
        from types import SimpleNamespace
        from app.services.runtime_settings_service import model_route_overrides
        from app.engine.litellm_transport import resolve_route
        self.require_enabled(principal)
        with self.engine.connect() as c: key=self._settings(c)['llm_model_option_key']
        if not key: raise AgentError('Configure an independent Lore Agent LLM',422)
        with Session(self.engine) as session:
            values=model_route_overrides(session,key,'chat')
        if not values: raise AgentError('Selected Agent LLM is unavailable',422)
        return resolve_route(SimpleNamespace(overrides=values,provider=lambda:values['provider_type'],model=lambda:values['model']))

    def resolve_image_route(self,principal,provider_account_id,model_id):
        from app.services import model_provider_service, provider_secret_service
        from app.agent.image_generation import (list_image_models_for_account,
            build_image_overrides, resolve_image_route_overrides)
        self.require_enabled(principal,'lore:image:generate')
        with self.engine.connect() as c:
            settings=self._settings(c)
        if (settings['image_provider_account_id'],settings['image_model_id'])!=(provider_account_id,model_id):
            raise AgentError('Image selection changed; stage and approve a new plan',409)
        with Session(self.engine) as session:
            account=model_provider_service.account_by_id(session,provider_account_id)
            if not model_provider_service.account_is_configured(account) or model_id not in {m['id'] for m in list_image_models_for_account(account)}:
                raise AgentError('Selected image account/model is unavailable',422)
            values={**build_image_overrides(account,provider_secret_service),'model':model_id}
        return resolve_image_route_overrides(values)

    def _settings(self, c):
        row = c.execute(select(m.settings.c.data).where(m.settings.c.id == 'settings')).scalar_one_or_none()
        return deepcopy(row) if row else SettingsUpdate().model_dump()

    def settings_view(self):
        from app.services.model_provider_service import active_options_for_runtime, list_accounts
        with self.engine.connect() as c:
            data = self._settings(c)
        with Session(self.engine) as s:
            choices = active_options_for_runtime(s, 'chat')
            data['llm_options'] = [{k: getattr(o, k) for k in ('key','label','provider_account_id','provider_type')} for o in choices]
            accounts = [a for a in list_accounts(s) if a.enabled and a.configured]
            chat_accounts = {o.provider_account_id for o in choices}
            data['llm_accounts'] = [{k: getattr(a, k) for k in ('id','alias','provider_type')} for a in accounts if a.id in chat_accounts]
            from app.agent.image_generation import list_image_models, list_image_models_for_account
            available = {a.id: list_image_models_for_account(a) for a in accounts}
            eligible = [a for a in accounts if available[a.id]]
            data['image_accounts'] = [{**{k: getattr(a, k) for k in ('id','alias','provider_type')},
                'image_model_ids': [model['id'] for model in available[a.id]]} for a in eligible]
            data['image_models'] = [model for provider in sorted({a.provider_type for a in eligible})
                for model in list_image_models(provider)
                if any(a.provider_type == provider and model['id'] in {m['id'] for m in available[a.id]} for a in eligible)]
        return data

    def update_settings(self, payload):
        data = SettingsUpdate.model_validate(payload if isinstance(payload, dict) else payload.model_dump()).model_dump()
        # Empty selections do not load optional image/runtime packages.
        if data['llm_model_option_key'] or data['image_provider_account_id'] or data['image_model_id']:
            from app.services.model_provider_service import active_options_for_runtime, list_accounts
            with Session(self.engine) as s:
                if data['llm_model_option_key'] and data['llm_model_option_key'] not in {o.key for o in active_options_for_runtime(s, 'chat')}:
                    raise AgentError('Inactive LLM selection', 422)
                if data['image_provider_account_id'] or data['image_model_id']:
                    from app.agent.image_generation import list_image_models_for_account
                    account = next((a for a in list_accounts(s) if a.id == data['image_provider_account_id'] and a.enabled and a.configured), None)
                    if not account or data['image_model_id'] not in {x['id'] for x in list_image_models_for_account(account)}:
                        raise AgentError('Invalid image selection', 422)
        with self._write() as c:
            exists = c.execute(select(m.settings.c.id)).first()
            if exists:
                self._put(c, m.settings, 'settings', data)
            else:
                c.execute(m.settings.insert().values(id='settings', owner_id='owner', data=data))
        return data

    def _authorize(self, c, principal, scope='lore:read'):
        settings = self._settings(c)
        if not settings['enabled']:
            raise AgentError('Lore Agent is disabled', 403)
        if principal.is_owner:
            if principal.id != 'owner':
                raise AgentError('Invalid principal', 403)
            return
        if not settings['mcp_enabled']:
            raise AgentError('MCP is disabled', 403)
        client = c.execute(select(m.clients.c.data).where(m.clients.c.id == principal.id)).scalar_one_or_none()
        if not client or not client['enabled'] or scope not in client['scopes'] or scope not in principal.scopes:
            raise AgentError('Scope denied or client revoked', 403)

    def require_enabled(self, principal, scope='lore:read'):
        with self.engine.connect() as c:
            self._authorize(c, principal, scope)

    def create_client(self, name, scopes):
        value = ClientInput.model_validate({'name': name, 'scopes': scopes})
        token = secrets.token_urlsafe(32)
        client = {'id': uid('client'), 'name': value.name, 'scopes': sorted(set(value.scopes)), 'enabled': True}
        with self._write() as c:
            c.execute(m.clients.insert().values(id=client['id'],token_digest=hashlib.sha256(token.encode()).hexdigest(),data=client))
        return {'client': client, 'token': token}

    def list_clients(self):
        with self.engine.connect() as c:
            return list(c.execute(select(m.clients.c.data).order_by(m.clients.c.id).limit(100)).scalars())

    def revoke_client(self, id):
        with self._write() as c:
            data = c.execute(select(m.clients.c.data).where(m.clients.c.id == id)).scalar_one_or_none()
            if not data:
                raise AgentError('Not found',404)
            data = dict(data, enabled=False)
            self._put(c,m.clients,id,data)

    def principal_for_token(self, raw_token):
        with self.engine.connect() as c:
            row = c.execute(select(m.clients).where(m.clients.c.token_digest == hashlib.sha256(raw_token.encode()).hexdigest())).mappings().first()
            if not row or not row['data']['enabled']:
                return None
            p = Principal(row['id'], frozenset(row['data']['scopes']))
            # Authenticate even a draft-only principal without requiring read.
            if not self._settings(c)['enabled'] or not self._settings(c)['mcp_enabled']:
                return None
            return p

    def _entity(self, c, kind, id):
        table = Character.__table__ if kind == 'character' else WorldSetting.__table__
        row = c.execute(select(table).where(table.c.id == id)).mappings().first()
        if not row:
            raise AgentError('Entity not found',404)
        allowed = CHARACTER_FIELDS if kind == 'character' else WORLD_FIELDS
        data = {k: deepcopy(row[k]) for k in allowed}
        if kind == 'character':
            data['trait_scores'] = {**DEFAULT_TRAIT_SCORES, **(data['trait_scores'] or {})}
        revision = c.execute(text(f'SELECT agent_revision FROM {table.name} WHERE id=:id'), {'id': id}).scalar_one()
        return data, revision

    def get_entity(self, principal, kind, id, fields=None):
        allowed = CHARACTER_FIELDS if kind == 'character' else WORLD_FIELDS
        if fields is not None and not set(fields).issubset(allowed):
            raise AgentError('Unsupported fields',422)
        with self.engine.connect() as c:
            self._authorize(c,principal)
            data, revision = self._entity(c,kind,id)
        selected = {k: data[k] for k in (fields if fields is not None else sorted(allowed))}
        return bounded({'id':id,'kind':kind,'revision':revision,'fields':selected})

    def search(self, principal, query='', kind='all', limit=10):
        from app.agent.contracts import SearchInput
        args = SearchInput(query=query,kind=kind,limit=limit)
        results=[]
        with self.engine.connect() as c:
            self._authorize(c,principal)
            for k, model, title in [('character',Character,'name'),('world',WorldSetting,'title')]:
                if args.kind not in (k,'all'): continue
                t=model.__table__
                rows=c.execute(select(t.c.id,t.c[title]).where(t.c[title].contains(args.query, autoescape=True)).order_by(t.c[title],t.c.id).limit(args.limit-len(results))).all()
                results.extend({'kind':k,'id':r[0],'label':r[1][:240]} for r in rows)
                if len(results)>=args.limit: break
        return {'items':results}

    def _normalize(self, kind, fields, creating=False):
        bounded(fields)
        allowed=CHARACTER_FIELDS if kind=='character' else WORLD_FIELDS
        if not fields or not set(fields).issubset(allowed):
            raise AgentError('Unsupported or empty authored fields',422)
        cls = (CharacterCreate if creating else CharacterUpdate) if kind=='character' else (WorldSettingCreate if creating else WorldSettingUpdate)
        obj=cls.model_validate(fields, strict=True)
        data=obj.model_dump(exclude_unset=not creating)
        data={k:v for k,v in data.items() if k in allowed}
        if kind=='character' and creating:
            data['forbidden_rules']=data.get('forbidden_rules') or DEFAULT_FORBIDDEN_RULES.copy()
        if kind=='world':
            from app.services.world_setting_service import normalize_authored_world_fields
            data=normalize_authored_world_fields(data)
        return bounded(data)

    def _hash_draft(self, d):
        d['preview_hash']=digest({k:d[k] for k in ('id','kind','target_id','version','base_revision','before','after','changes')})
        return d

    def _new_draft(self,c,principal,kind,after,target_id=None,session_id=None,before=None,base_revision=None,changes=None):
        if session_id:
            self._get(c,m.sessions,session_id,principal)
            from sqlalchemy import func
            count=c.execute(select(func.count()).select_from(m.drafts).where(m.drafts.c.data['session_id'].as_string()==session_id)).scalar_one()
            if count>=100: raise AgentError('Session draft limit reached; start a new session',422)
        d={'id':uid('draft'),'session_id':session_id,'kind':kind,'target_id':target_id,'version':1,'base_revision':base_revision,'before':before,'after':after,'changes':changes if changes is not None else after,'preview_hash':'','status':'draft','warnings':[],'result':None}
        self._hash_draft(d)
        c.execute(m.drafts.insert().values(id=d['id'],owner_id=principal.id,data=d))
        return public(d)

    def create_draft(self, principal, kind, fields, target_id=None, session_id=None):
        if kind not in ('character','world'): raise AgentError('Unsupported draft kind',422)
        normalized=self._normalize(kind,fields,not target_id)
        with self._write() as c:
            self._authorize(c,principal,'lore:draft')
            snapshot,rev=self._entity(c,kind,target_id) if target_id else (None,None)
            if snapshot is not None and kind=='character' and 'trait_scores' in fields:
                normalized['trait_scores']={**snapshot['trait_scores'],**fields['trait_scores']}
            before={k:snapshot[k] for k in normalized} if snapshot is not None else None
            after=normalized
            changes={k:v for k,v in normalized.items() if before is None or before.get(k)!=v}
            if not changes: raise AgentError('No changes',422)
            return self._new_draft(c,principal,kind,after,target_id,session_id,before,rev,changes)

    def get_draft(self,id,principal):
        with self.engine.connect() as c:
            self._authorize(c,principal,'lore:draft')
            return public(self._get(c,m.drafts,id,principal))

    def revise_draft(self,principal,id,expected_version,fields):
        with self._write() as c:
            self._authorize(c,principal,'lore:draft')
            d=self._get(c,m.drafts,id,principal)
            if d['version']!=expected_version or d['status'] in ('committed','conflict'):
                raise AgentError('Draft version/status conflict')
            if d['kind'] == 'image_generation':
                return self._revise_image(c, principal, d, fields)
            if d['kind'] not in ('character','world'):
                raise AgentError('Image plans are frozen; stage a new plan',422)
            normalized=self._normalize(d['kind'],fields)
            if d['target_id']:
                snapshot,revision=self._entity(c,d['kind'],d['target_id'])
                if revision!=d['base_revision']: raise AgentError('Entity changed; stage a fresh draft')
                for k in normalized:
                    if k not in d['before']: d['before'][k]=snapshot[k]
                if d['kind']=='character' and 'trait_scores' in fields:
                    normalized['trait_scores']={**d['after'].get('trait_scores',snapshot['trait_scores']),**fields['trait_scores']}
            d['after'].update(normalized)
            d['changes']={k:v for k,v in d['after'].items() if d['before'] is None or d['before'].get(k)!=v}
            d.update(version=d['version']+1,status='draft',warnings=[])
            d.pop('_approval',None); d.pop('_approval_requested',None)
            self._hash_draft(d)
            self._put(c,m.drafts,id,d)
            return public(d)

    def approve_draft(self,principal,id,expected_version,preview_hash):
        if not principal.is_owner or principal.id!='owner': raise AgentError('Owner approval required',403)
        with self._write() as c:
            self._authorize(c,principal)
            d=self._get(c,m.drafts,id,principal)
            self._match(d,expected_version,preview_hash)
            if d['status'] not in ('draft','approved'): raise AgentError('Draft cannot be approved')
            if d['kind'] == 'image_generation': self._validate_image_choice(c,d)
            d['_approval']={'owner':'owner','version':expected_version,'preview_hash':preview_hash,'base_revision':d['base_revision']}
            d['status']='approved'
            self._put(c,m.drafts,id,d)
            return public(d)

    def request_approval(self,principal,id):
        # Record a durable review request, never an approval grant.
        with self._write() as c:
            self._authorize(c,principal,'lore:draft')
            d=self._get(c,m.drafts,id,principal)
            if d['status']=='conflict': raise AgentError('Stage a new preview before requesting approval')
            if d['status']=='draft':
                d['_approval_requested']=True
                warning='Owner approval requested for this exact preview.'
                if warning not in d['warnings']: d['warnings'].append(warning)
                self._put(c,m.drafts,id,d)
            return {'draft':public(d),'approval_required':d['status'] not in ('approved','committed'),'owner_action':'review_and_approve'}

    def _match(self,d,version,preview_hash):
        if d['version']!=version or not hmac.compare_digest(d['preview_hash'],preview_hash):
            raise AgentError('Preview version/hash conflict')

    def _approved(self,d,version,preview_hash):
        self._match(d,version,preview_hash)
        a=d.get('_approval')
        if d['status']!='approved' or a!={'owner':'owner','version':version,'preview_hash':preview_hash,'base_revision':d['base_revision']}:
            raise AgentError('Matching owner approval required',403)

    def _replay(self,c,request_id,payload,principal):
        row=c.execute(select(m.operations).where(m.operations.c.id==request_id)).mappings().first()
        if not row: return None
        if row['owner_id']!=principal.id or row['data']['_payload_hash']!=digest(payload):
            raise AgentError('Request id already used with different payload or principal')
        return public(row['data'])

    def _receipt(self,c,principal,request_id,payload,kind,result,status='completed',error=None):
        data={'request_id':request_id,'status':status,'kind':kind,'result':result,'error':error,'_payload_hash':digest(payload)}
        c.execute(m.operations.insert().values(id=request_id,owner_id=principal.id,data=data))
        return public(data)

    def get_operation(self,request_id,principal):
        with self.engine.connect() as c:
            self._authorize(c,principal)
            row=c.execute(select(m.operations).where(m.operations.c.id==request_id)).mappings().first()
            if not row or (not principal.is_owner and row['owner_id']!=principal.id):
                return {'request_id':request_id,'status':'unknown','kind':'unknown','result':None,'error':None}
            return public(row['data'])

    async def commit_draft(self,principal,draft_id,request_id,expected_version,preview_hash):
        with self.engine.connect() as c:
            self._authorize(c,principal,'lore:commit')
            d=self._get(c,m.drafts,draft_id,principal)
        if d['kind']=='image_generation':
            from app.agent.image_generation import execute_image_plan
            return await execute_image_plan(self,principal,draft_id,request_id,expected_version,preview_hash)
        return self._commit_domain(principal,draft_id,request_id,expected_version,preview_hash)

    def commit_form(self,principal,draft_id,request_id,expected_version,preview_hash,manual_fields,baseline_fields):
        """Explicit owner Save, never a tool entry point or ordinary CRUD follow-up."""
        from app.agent.contracts import CommitFormBody
        if not principal.is_owner or principal.id!='owner':
            raise AgentError('Owner form commit required',403)
        value=CommitFormBody(request_id=request_id,expected_version=expected_version,preview_hash=preview_hash,
                             manual_fields=manual_fields,baseline_fields=baseline_fields)
        bounded(value.manual_fields)
        bounded(value.baseline_fields,1048576)
        return self._commit_domain(principal,draft_id,request_id,expected_version,preview_hash,
                                   form_context={'manual_fields':value.manual_fields,'baseline_fields':value.baseline_fields})

    def _form_fields(self,d,context):
        if d['kind'] not in ('character','world'):
            raise AgentError('Native form commit supports character/world drafts only',422)
        is_character=d['kind']=='character'
        manual_allowed={'avatar_url','tts_provider','tts_model','tts_voice_style','tts_sample_text'} if is_character else {'thumbnail_url'}
        authored=CHARACTER_FIELDS if is_character else WORLD_FIELDS
        manual=context['manual_fields']; baseline=context['baseline_fields']
        if not set(manual).issubset(manual_allowed):
            raise AgentError('Unsupported manual form fields',422)
        if set(baseline)!=(authored if d['target_id'] else set()):
            raise AgentError('Complete authored editor baseline required for updates; empty baseline required for creates',422)
        cls=CharacterUpdate if is_character else WorldSettingUpdate
        # Exact allowlists precede validation: ordinary request models may ignore
        # extras, but this boundary must never admit credentials or arbitrary attrs.
        clean=cls.model_validate(manual,strict=True).model_dump(exclude_unset=True)
        baseline=cls.model_validate(baseline,strict=True).model_dump(exclude_unset=True)
        return clean,baseline

    def _rebase_form(self,d,snapshot,revision,baseline):
        # Full editor-open baseline catches competing authored changes outside
        # the proposal. Its partial before snapshot also binds the original draft
        # so reopening a stale proposal cannot authorize an authored overwrite.
        if snapshot!=baseline or not isinstance(d['before'],dict) or any(snapshot.get(k)!=v for k,v in d['before'].items()):
            raise AgentError('Authored fields changed since preview or editor baseline')
        if revision!=d['base_revision']:
            d.update(base_revision=revision,version=d['version']+1)
            self._hash_draft(d)
            # The owner explicitly approved the original preview and Save binds
            # unchanged authored changes plus these manual fields in one journal.
            d['_approval']={'owner':'owner','version':d['version'],'preview_hash':d['preview_hash'],'base_revision':revision}
            self._approved(d,d['version'],d['preview_hash'])

    def _commit_domain(self,principal,draft_id,request_id,expected_version,preview_hash,*,form_context=None):
        payload={'draft_id':draft_id,'expected_version':expected_version,'preview_hash':preview_hash}
        if form_context is not None:
            if not principal.is_owner or principal.id!='owner': raise AgentError('Owner form commit required',403)
            # Hash raw validated request values, before DTO normalization. Even
            # semantically equivalent but altered JSON must not reuse this key.
            payload.update(operation='owner_form_commit',**form_context)
        conflict=False
        with self._write() as c:
            self._authorize(c,principal,'lore:commit')
            replay=self._replay(c,request_id,payload,principal)
            if replay: return replay
            d=self._get(c,m.drafts,draft_id,principal)
            self._match(d,expected_version,preview_hash)
            if d['status']=='committed':
                if form_context is not None: raise AgentError('Draft already committed; reconcile the original request')
                return self._receipt(c,principal,request_id,payload,d['kind'],d['result'])
            self._approved(d,expected_version,preview_hash)
            manual,baseline=self._form_fields(d,form_context) if form_context is not None else ({},None)
            if d['target_id']:
                kind=d['after'].get('target_kind','character') if d['kind']=='image_registration' else d['kind']
                try: snapshot,rev=self._entity(c,kind,d['target_id'])
                except AgentError:
                    if form_context is not None: raise
                    rev=None
                if form_context is not None:
                    self._rebase_form(d,snapshot,rev,baseline)
                elif rev!=d['base_revision']:
                    d.update(status='conflict'); d.pop('_approval',None)
                    self._put(c,m.drafts,draft_id,d); conflict=True
            if not conflict:
                if d['kind']=='image_registration':
                    result=self._register_images(c,principal,d)
                else:
                    model=Character if d['kind']=='character' else WorldSetting
                    t=model.__table__
                    target=d['target_id'] or uid('char' if d['kind']=='character' else 'world')
                    if d['target_id']:
                        # Explicit CAS, even though BEGIN IMMEDIATE serializes writers.
                        statement=t.update().where(t.c.id==target).where(text('agent_revision=:base_revision')).values(**d['changes'],**manual,updated_at=utc_now())
                        changed=c.execute(statement,{'base_revision':d['base_revision']}).rowcount
                        if changed!=1: raise AgentError('Entity revision conflict')
                    else:
                        obj=model(id=target,**d['after'],**manual)
                        c.execute(t.insert().values(**obj.model_dump()))
                    result={'id':target,'kind':d['kind']}
                    if form_context is not None:
                        result.update(draft_id=draft_id,version=d['version'],preview_hash=d['preview_hash'])
                d.update(status='committed',result=result); d.pop('_approval',None)
                self._put(c,m.drafts,draft_id,d)
                receipt=self._receipt(c,principal,request_id,payload,d['kind'],result)
        if conflict: raise AgentError('Entity changed since preview')
        return receipt

    def _session_view(self,c,id,principal):
        data=self._get(c,m.sessions,id,principal)
        # Filter by session in SQL, not after a library-wide LIMIT.
        ds=c.execute(select(m.drafts.c.data).where(m.drafts.c.data['session_id'].as_string()==id).limit(100)).scalars()
        result=public(data); result['drafts']=[public(d) for d in ds]
        result['image_contexts']=deepcopy(data.get('_image_contexts',[]))
        return result

    def create_session(self,principal,request_id):
        payload={'kind':'session'}
        with self._write() as c:
            self._authorize(c,principal,'lore:draft')
            previous=self._replay(c,request_id,payload,principal)
            if previous: return self._session_view(c,previous['result']['id'],principal)
            id=uid('session')
            data={'id':id,'title':'Lore Agent','messages':[],'busy':False,'last_error':None,'_owner_id':principal.id,'_created_at':utc_now().isoformat()}
            c.execute(m.sessions.insert().values(id=id,owner_id=principal.id,data=data))
            self._receipt(c,principal,request_id,payload,'session',{'id':id})
            return self._session_view(c,id,principal)

    def list_sessions(self,principal):
        with self.engine.connect() as c:
            self._authorize(c,principal)
            query=select(m.sessions.c.id).order_by(m.sessions.c.data['_created_at'].as_string().desc()).limit(20)
            if not principal.is_owner: query=query.where(m.sessions.c.owner_id==principal.id)
            ids=c.execute(query).scalars().all()
            return [self._session_view(c,id,principal) for id in ids]

    def get_session(self,id,principal):
        with self.engine.connect() as c:
            self._authorize(c,principal)
            return self._session_view(c,id,principal)

    def get_context(self,context,principal=None):
        validated=[ContextSelection.model_validate(x.model_dump() if hasattr(x,'model_dump') else x) for x in context]
        if len(validated)>8: raise AgentError('Too much context',422)
        with self.engine.connect() as c:
            if principal: self._authorize(c,principal)
            values=[]
            for item in validated:
                data,revision=self._entity(c,item.kind,item.id)
                values.append({'kind':item.kind,'id':item.id,'revision':revision,'fields':{k:data[k] for k in item.fields}})
        return bounded(values,32000)

    def session_fields(self,principal,session_id,kind,id):
        with self.engine.connect() as c:
            self._authorize(c,principal)
            data=self._get(c,m.sessions,session_id,principal)
            context=data.get('_context',[])
            # Normal chat discovers relevant entities through bounded typed
            # tools. Advanced explicit selections still narrow access exactly.
            if not context:
                return CHARACTER_FIELDS if kind=='character' else WORLD_FIELDS
            selected=[x for x in context if x['kind']==kind and x['id']==id]
        if not selected: raise AgentError('Explicit context selection required for this entity',403)
        return set().union(*(set(x['fields']) for x in selected))

    def _failed_predecessors(self, c, principal, data):
        # Legacy user messages have no request id. The receipt was inserted in
        # the same serialized transaction as its message. Verify the last
        # session turn, its original payload, and the absence of active turns.
        sid = data['id']
        turns = select(m.operations).where(
            m.operations.c.data['kind'].as_string() == 'agent_turn',
            m.operations.c.data['result']['session_id'].as_string() == sid)
        rows = c.execute(turns.order_by(text('rowid DESC')).limit(101)).mappings().all()
        users = [msg for msg in data['messages'] if msg['role'] == 'user']
        if (not rows or len(rows) != len(users) or data['messages'][-1]['role'] != 'user'
                or any(row['data']['status'] not in ('completed', 'failed') for row in rows)):
            raise AgentError('Cannot verify failed session turn')
        row = rows[0]
        op, rid, msg = row['data'], row['id'], users[-1]
        payload = dict(session_id=sid, request_id=rid, content=msg['content'], context=data['_context'])
        if (row['owner_id'] != principal.id or data['_owner_id'] != principal.id
                or op['request_id'] != rid or op['status'] != 'failed'
                or op['error'] != data['last_error'] or msg.get('request_id', rid) != rid
                or op['_payload_hash'] != digest(payload)):
            raise AgentError('Cannot verify failed session turn')
        # Guard-only failures append a receipt but no graph message. The
        # checkpoint can therefore refer to an earlier failed turn (B, not C).
        return {r['id']: u['content'] for r, u in zip(rows, reversed(users))
                if r['owner_id'] == principal.id and r['data']['status'] == 'failed'
                and r['data']['request_id'] == r['id'] and u.get('request_id', r['id']) == r['id']}

    def rotate_run_lineage(self, principal, session_id, request_id):
        with self._write() as c:
            data = self._get(c, m.sessions, session_id, principal)
            if not data['busy'] or data.get('_active_run') != request_id or data.pop('_rotate_from', None) != request_id:
                raise AgentError('Run ownership conflict')
            data['_lineage'] = uid('lineage')
            self._put(c, m.sessions, session_id, data)
            return data['_lineage']

    def accept_run(self,principal,session_id,request_id,content,context):
        value=MessageInput.model_validate({'request_id':request_id,'content':content,'context':[x.model_dump() if hasattr(x,'model_dump') else x for x in context]})
        payload={'session_id':session_id,**value.model_dump()}
        with self._write() as c:
            self._authorize(c,principal,'lore:draft')
            replay=self._replay(c,request_id,payload,principal)
            if replay: return {'claimed':False,'operation':replay}
            data=self._get(c,m.sessions,session_id,principal)
            if data['busy']: raise AgentError('Session is busy; reconcile its accepted run')
            if len(data['messages'])>=100: raise AgentError('Session message limit reached; start a new session',422)
            if data.get('_active_run'): raise AgentError('Session has an active run')
            failed = self._failed_predecessors(c, principal, data) if data['last_error'] else {}
            seed = data['messages'][:] if failed else []
            if failed: data['_rotate_from'] = request_id
            data['messages'].append({'id':uid('msg'),'request_id':request_id,'role':'user','content':content})
            context_values=[]
            for item in value.context:
                entity,revision=self._entity(c,item.kind,item.id)
                context_values.append({'kind':item.kind,'id':item.id,'revision':revision,'fields':{k:entity[k] for k in item.fields}})
            bounded(context_values,32000)
            data.update(busy=True,last_error=None,_active_run=request_id,_context=[x.model_dump() for x in value.context])
            if len(data['messages'])==1: data['title']=content[:80]
            self._put(c,m.sessions,session_id,data)
            op=self._receipt(c,principal,request_id,payload,'agent_turn',{'session_id':session_id},'running')
            return {'claimed':True,'operation':op,'failed':failed,'seed':seed,'lineage':data.get('_lineage')}

    def complete_run(self,principal,session_id,request_id,content=None,error=None):
        if content is not None and (not isinstance(content,str) or len(content)>32000): raise AgentError('Invalid response size',422)
        with self._write() as c:
            # Completion of accepted work remains allowed after disable/revoke.
            data=self._get(c,m.sessions,session_id,principal)
            op=self._get(c,m.operations,request_id,principal)
            if data.get('_active_run')!=request_id:
                if op['status']!='running': return self._session_view(c,session_id,principal)
                raise AgentError('Run ownership conflict')
            if content:
                data['messages'].append({'id':uid('msg'),'role':'assistant','content':content})
            data.update(busy=False,last_error=error[:500] if error else None); data.pop('_active_run',None)
            data.pop('_rotate_from', None)
            op.update(status='failed' if error else 'completed',error=error[:500] if error else None)
            self._put(c,m.sessions,session_id,data); self._put(c,m.operations,request_id,op)
            return self._session_view(c,session_id,principal)

    def _revise_image(self, c, principal, d, fields):
        from types import SimpleNamespace
        from app.agent.image_generation import validate_plan
        if not fields or not set(fields).issubset({'prompt','count','options'}):
            raise AgentError('Image revision allows prompt, count and options only; mode/reference requires owner choice',422)
        bounded(fields,12000)
        plan = d['after']
        proposed = ImagePlanInput(target_id=plan['target_id'], target_kind=plan['target_kind'],
                                 **{k:fields.get(k,plan[k]) for k in ('prompt','count','options')})
        plan.update({k:getattr(proposed,k) for k in ('prompt','count','options')})
        try: validate_plan({**plan,'generation_mode':'new','reference':None}, SimpleNamespace(provider=plan['provider_type'],sdk_provider=plan['provider_type'],model=plan['model_id']))
        except ValueError: raise AgentError('Image options are not supported by the selected model',422) from None
        context, _, revision = self._image_inventory(c,self._draft_principal(c,d),d['target_id'],plan['target_kind'])
        plan.update(generation_mode='undecided' if context['has_existing_images'] else 'new', reference=None,
                    owner_choice_confirmed=False, image_context=context, target_revision=revision)
        d.update(version=d['version']+1,base_revision=revision,status='draft',warnings=[],changes=deepcopy(plan))
        for key in ('_approval','_approval_requested','_image_choice'): d.pop(key,None)
        self._hash_draft(d); self._put(c,m.drafts,d['id'],d)
        return public(d)

    def _image_inventory(self, c, principal, target_id, target_kind):
        from app.agent.contracts import ImageContextInput
        from sqlalchemy import func
        ImageContextInput(target_id=target_id, target_kind=target_kind)
        _, revision = self._entity(c, target_kind, target_id)
        t = Character.__table__ if target_kind == 'character' else WorldSetting.__table__
        url_column = t.c.avatar_url if target_kind == 'character' else t.c.thumbnail_url
        avatar = c.execute(select(url_column).where(t.c.id == target_id)).scalar_one()
        sources = []
        if avatar:
            sources.append({'id':'avatar', 'label':'Current avatar' if target_kind == 'character' else 'Current thumbnail',
                            'kind':'avatar', 'is_default':True, '_url':avatar})
        asset_count = 0
        if target_kind == 'character':
            a = CharacterAsset.__table__
            condition = (a.c.character_id == target_id) & (a.c.image_url != '')
            asset_count = c.execute(select(func.count()).select_from(a).where(condition)).scalar_one()
            rows = c.execute(select(a).where(condition).order_by(a.c.is_default.desc(), a.c.id).limit(20)).mappings()
            for row in rows:
                sources.append({'id':'asset:'+row['id'], 'label':row['label'][:120], 'kind':'asset',
                                'is_default':bool(row['is_default']), '_url':row['image_url']})
        # Only this principal's retained outputs are candidates (owner UI may
        # inspect a client draft using that draft's principal inventory).
        jobs = m.image_jobs
        outputs = m.image_outputs
        condition = (jobs.c.owner_id == principal.id) & (jobs.c.data['_plan']['target_id'].as_string() == target_id)
        condition &= func.coalesce(jobs.c.data['_plan']['target_kind'].as_string(), 'character') == target_kind
        rows = c.execute(select(outputs.c.id, outputs.c.digest).join(jobs, jobs.c.id == outputs.c.job_id)
                         .where(condition).order_by(outputs.c.id).limit(8)).mappings()
        for row in rows:
            sources.append({'id':'output:'+row['id'], 'label':'Retained generated image', 'kind':'output',
                            'is_default':False, '_output':row['id'], '_digest':row['digest']})
        fingerprints = []
        candidates = []
        for source in sources:
            item = public(source)
            try:
                image = self._candidate_bytes(c, principal, source)
                sha = hashlib.sha256(image['data']).hexdigest()
                item['available'] = True
            except (AgentError, ValueError, OSError):
                sha = None
                item.update(available=False, reason='Original unavailable: upload a local PNG, JPEG or WebP image; remote URLs are not fetched.')
            source['_sha256'] = sha
            fingerprints.append({'source':source, 'sha256':sha})
            candidates.append(item)
        context = {'has_existing_images':bool(sources or asset_count), 'requires_choice':bool(sources or asset_count),
                   'candidates':candidates, 'inventory_digest':digest({'revision':revision, 'asset_count':asset_count, 'sources':fingerprints})}
        return context, sources, revision

    def _candidate_bytes(self, c, principal, source):
        from pathlib import Path
        from urllib.parse import urlsplit
        from app.core.config import get_settings
        from app.services.image_reference_cleanup import local_source
        from app.services.asset_image_service import read_local_image, detect_mime
        if source.get('_output'):
            row = c.execute(select(m.image_outputs).where(m.image_outputs.c.id == source['_output'],
                           m.image_outputs.c.owner_id == principal.id)).mappings().first()
            if not row or row['size'] != len(row['data']) or hashlib.sha256(row['data']).hexdigest() != row['digest']:
                raise AgentError('Reference output unavailable',422)
            data, mime = row['data'], row['mime_type']
        else:
            url = source.get('_url', '')
            # Even loopback absolute URLs are remote here: never guess which
            # server owns a colliding /uploads pathname.
            if urlsplit(url).scheme or urlsplit(url).netloc:
                raise AgentError('Remote reference must be uploaded locally',422)
            canonical = local_source(url)
            if canonical is None: raise AgentError('Unsafe reference source',422)
            settings = get_settings()
            data = read_local_image(canonical, Path(settings.upload_root).expanduser(), settings.shared_upload_root)
            mime = detect_mime(data)
        if len(data) > 8 * 1024 * 1024: raise AgentError('Reference exceeds image byte budget',422)
        self._verified_image(data, mime)
        return {'data':data, 'mime_type':mime, 'sha256':hashlib.sha256(data).hexdigest()}

    def image_context(self, principal, target_id, target_kind='character', *, session_id=None, request_id=None):
        with self.engine.connect() as c:
            self._authorize(c, principal)
            context, _, _ = self._image_inventory(c, principal, target_id, target_kind)
            entity, _ = self._entity(c, target_kind, target_id)
        if session_id is not None:
            # A read tool may publish its bounded, URL-free UI projection. It
            # never chooses a reference, creates a plan or grants paid consent.
            item={'target_kind':target_kind,'target_id':target_id,
                  'target_label':entity['name' if target_kind=='character' else 'title'],
                  'context':deepcopy(context)}
            with self._write() as c:
                self._authorize(c,principal)
                data=self._get(c,m.sessions,session_id,principal)
                if data.get('_owner_id')!=principal.id:
                    raise AgentError('Image context belongs to another session',403)
                if request_id is not None and (not data.get('busy') or data.get('_active_run')!=request_id):
                    raise AgentError('Image context run changed',409)
                items=[x for x in data.get('_image_contexts',[]) if (x['target_kind'],x['target_id'])!=(target_kind,target_id)]
                data['_image_contexts']=bounded((items+[item])[-8:],256000)
                self._put(c,m.sessions,session_id,data)
        view = self.settings_view()
        account = next((a for a in view['image_accounts'] if a['id'] == view['image_provider_account_id']), None)
        model = next((x for x in view['image_models'] if account and x['id'] == view['image_model_id'] and x['provider_type'] == account['provider_type']), None)
        return {**context, 'supports_reference':bool(model and model['supports_reference']),
                'model_id':model['id'] if model else None}

    def image_reference_preview(self, principal, target_kind, target_id, reference_id):
        if not principal.is_owner or principal.id != 'owner': raise AgentError('Owner access required',403)
        with self.engine.connect() as c:
            self._authorize(c, principal)
            _, sources, _ = self._image_inventory(c, principal, target_id, target_kind)
            source = next((s for s in sources if s['id'] == reference_id), None)
            source_principal = principal
            if source is None and reference_id.startswith('output:'):
                # Owner preview of an external client's draft still requires an
                # exact retained output joined to this target, never an arbitrary blob.
                j, o = m.image_jobs, m.image_outputs
                from sqlalchemy import func
                row = c.execute(select(o.c.id,o.c.owner_id).join(j,j.c.id == o.c.job_id).where(
                    o.c.id == reference_id.removeprefix('output:'),
                    j.c.data['_plan']['target_id'].as_string() == target_id,
                    func.coalesce(j.c.data['_plan']['target_kind'].as_string(),'character') == target_kind)).mappings().first()
                if row:
                    source = {'_output':row['id']}
                    source_principal = Principal(row['owner_id'])
            if source is None: raise AgentError('Reference does not belong to this target',404)
            try: return self._candidate_bytes(c, source_principal, source)
            except (ValueError, OSError): raise AgentError('Reference original unavailable; upload a local image',422) from None

    def _draft_principal(self, c, d):
        owner_id = c.execute(select(m.drafts.c.owner_id).where(m.drafts.c.id == d['id'])).scalar_one()
        return Principal(owner_id)

    def _reference_capability(self, plan):
        from app.agent.image_generation import list_image_models
        model = next((x for x in list_image_models(plan['provider_type']) if x['id'] == plan['model_id']), None)
        if not model: raise AgentError('Selected image model is unavailable; explicitly select a supported model',422)
        if plan['generation_mode'] == 'reference' and not model['supports_reference']:
            raise AgentError('Selected image model does not support reference editing; explicitly change the model or choose new',422)

    def choose_image_mode(self, principal, draft_id, expected_version, generation_mode, reference_id=None):
        from app.agent.contracts import NewImageChoiceInput
        NewImageChoiceInput(expected_version=expected_version, generation_mode=generation_mode, reference_id=reference_id)
        if not principal.is_owner or principal.id != 'owner': raise AgentError('Owner choice required',403)
        with self._write() as c:
            self._authorize(c, principal)
            d = self._get(c, m.drafts, draft_id, principal)
            if d['kind'] != 'image_generation' or d['version'] != expected_version or d['status'] not in ('draft','approved'):
                raise AgentError('Image draft version/status conflict')
            p = self._draft_principal(c, d)
            plan = d['after']
            context, sources, revision = self._image_inventory(c, p, d['target_id'], plan['target_kind'])
            plan.update(generation_mode=generation_mode, image_context=context, owner_choice_confirmed=True,
                        reference=None, target_revision=revision)
            self._reference_capability(plan)
            if generation_mode == 'reference':
                source = next((s for s in sources if s['id'] == reference_id), None)
                if source is None: raise AgentError('Reference does not belong to this target',422)
                try: image = self._candidate_bytes(c, p, source)
                except (ValueError, OSError): raise AgentError('Reference original unavailable; upload a local image',422) from None
                # Bind the snapshot to the inventory just displayed/inspected.
                refreshed, _, _ = self._image_inventory(c, p, d['target_id'], plan['target_kind'])
                if refreshed != context or source['_sha256'] != image['sha256']: raise AgentError('Reference changed during selection; select again')
                snapshot = uid('reference')
                c.execute(m.image_references.insert().values(id=snapshot, owner_id=p.id, draft_id=draft_id,
                    target_id=d['target_id'], target_kind=plan['target_kind'], source_id=reference_id,
                    digest=image['sha256'], mime_type=image['mime_type'], data=image['data']))
                plan['reference'] = {'snapshot_id':snapshot, 'source_id':reference_id, 'sha256':image['sha256'], 'mime_type':image['mime_type']}
            d.update(base_revision=revision, version=d['version']+1, status='draft', warnings=[], changes=deepcopy(plan))
            d.pop('_approval',None); d.pop('_approval_requested',None)
            d['_image_choice'] = {'owner':'owner', 'plan_hash':digest(plan)}
            self._hash_draft(d)
            self._put(c,m.drafts,draft_id,d)
            return public(d)

    def _validate_image_choice(self, c, d):
        plan = d['after']
        mode = plan.get('generation_mode')
        if mode not in ('new','reference'): raise AgentError('Choose consistency reference or new design in the owner review before approval',422)
        p = self._draft_principal(c, d)
        context, _, revision = self._image_inventory(c,p,d['target_id'],plan.get('target_kind','character'))
        if revision != d['base_revision'] or revision != plan['target_revision'] or context != plan.get('image_context'):
            raise AgentError('Image context or original changed; select again and reapprove')
        choice = d.get('_image_choice')
        if context['has_existing_images'] or mode == 'reference' or plan.get('owner_choice_confirmed'):
            if choice != {'owner':'owner','plan_hash':digest(plan)} or not plan.get('owner_choice_confirmed'):
                raise AgentError('Explicit owner image choice required',403)
        if mode == 'new' and plan.get('reference') is not None: raise AgentError('New mode cannot contain a reference',422)
        self._reference_capability(plan)
        if mode == 'reference': self._snapshot(c,p,plan,d)

    def _snapshot(self, c, principal, plan, d=None):
        ref = plan.get('reference')
        if not isinstance(ref,dict): raise AgentError('Reference snapshot required',422)
        row = c.execute(select(m.image_references).where(m.image_references.c.id == ref.get('snapshot_id'))).mappings().first()
        if not row or (not principal.is_owner and row['owner_id'] != principal.id): raise AgentError('Reference unavailable',404)
        d = d or self._get(c,m.drafts,row['draft_id'],principal)
        owner = self._draft_principal(c,d)
        if (d['kind'] != 'image_generation' or d['after'] != plan or plan.get('generation_mode') != 'reference'
            or row['draft_id'] != d['id'] or row['owner_id'] != owner.id
            or row['target_id'] != plan['target_id'] or row['target_kind'] != plan['target_kind']
            or ref != {'snapshot_id':row['id'],'source_id':row['source_id'],'sha256':row['digest'],'mime_type':row['mime_type']}
            or hashlib.sha256(row['data']).hexdigest() != row['digest']
            or d.get('_image_choice') != {'owner':'owner','plan_hash':digest(plan)}):
            raise AgentError('Reference snapshot binding conflict')
        self._verified_image(row['data'],row['mime_type'])
        return {'data':row['data'],'mime_type':row['mime_type'],'sha256':row['digest']}

    def load_image_reference(self, principal, plan):
        with self.engine.connect() as c:
            self._authorize(c,principal,'lore:image:generate')
            if plan.get('generation_mode') == 'new' and plan.get('reference') is None: return None
            if plan.get('generation_mode') != 'reference': raise AgentError('Image mode is undecided',422)
            return self._snapshot(c,principal,plan)

    def draft_image_reference(self, principal, draft_id):
        if not principal.is_owner or principal.id != 'owner': raise AgentError('Owner access required',403)
        with self.engine.connect() as c:
            self._authorize(c,principal)
            d = self._get(c,m.drafts,draft_id,principal)
            return self._snapshot(c,principal,d['after'],d)

    def plan_image(self,principal,target_id,prompt,count=1,options=None,reference_output_id=None,session_id=None,target_kind='character'):
        value=ImagePlanInput(target_id=target_id,target_kind=target_kind,prompt=prompt,count=count,options=options or {},reference_output_id=reference_output_id)
        bounded(value.model_dump(),12000)
        view=self.settings_view()
        account=next((a for a in view['image_accounts'] if a['id']==view['image_provider_account_id']),None)
        model=next((x for x in view['image_models'] if x['id']==view['image_model_id'] and account and x.get('provider_type',account['provider_type'])==account['provider_type']),None)
        if not account or not model: raise AgentError('Configure an image model independently in settings',422)
        if reference_output_id: raise AgentError('Select references through the owner image-choice review',422)
        # Provider options are bounded and validated again by the worker before dispatch.
        if set(value.options)-{'size','quality','aspect_ratio','background','output_format'} or any(not isinstance(v,str) or len(v)>64 for v in value.options.values()):
            raise AgentError('Unsupported image options',422)
        if reference_output_id: self.image_output(reference_output_id,principal)
        with self._write() as c:
            self._authorize(c,principal,'lore:image:generate')
            _,revision=self._entity(c,value.target_kind,target_id)
            plan={'provider_account_id':account['id'],'provider_type':account['provider_type'],'model_id':model['id'],'target_id':target_id,'target_kind':value.target_kind,'target_revision':revision,'prompt':prompt,'count':count,'options':value.options,'reference_output_id':reference_output_id}
            context, _, _ = self._image_inventory(c,principal,target_id,value.target_kind)
            plan.update(generation_mode='undecided' if context['has_existing_images'] else 'new',
                        image_context=context, owner_choice_confirmed=False, reference=None)
            from types import SimpleNamespace
            from app.agent.image_generation import validate_plan
            try:
                validate_plan({**plan,'generation_mode':'new','reference':None},SimpleNamespace(provider=account['provider_type'],sdk_provider=account['provider_type'],model=model['id']))
            except ValueError:
                raise AgentError('Image options are not supported by the selected model',422) from None
            return self._new_draft(c,principal,'image_generation',plan,target_id,session_id,base_revision=revision)

    def claim_image_generation(self,principal,draft_id,request_id,expected_version,preview_hash):
        payload={'draft_id':draft_id,'expected_version':expected_version,'preview_hash':preview_hash}
        with self._write() as c:
            self._authorize(c,principal,'lore:image:generate')
            self._authorize(c,principal,'lore:commit')
            replay=self._replay(c,request_id,payload,principal)
            d=self._get(c,m.drafts,draft_id,principal)
            if replay:
                return {'claimed':False,'operation':replay,'job_id':replay['result']['job_id'],'plan':d['after']}
            self._match(d,expected_version,preview_hash)
            if d['kind']!='image_generation': raise AgentError('Not an image generation plan',422)
            if d['status']=='committed':
                old=self._get(c,m.image_jobs,d['result']['job_id'],principal)
                op=self._receipt(c,principal,request_id,payload,'image_generation',d['result'],old['status'],old['error'])
                return {'claimed':False,'operation':op,'job_id':old['id'],'plan':d['after']}
            self._approved(d,expected_version,preview_hash)
            self._validate_image_choice(c,d)
            _,revision=self._entity(c,d['after'].get('target_kind','character'),d['target_id'])
            if revision!=d['base_revision']: raise AgentError('Image target changed since preview')
            job_id=uid('image')
            job={'id':job_id,'status':'running','outputs':[],'error':None,'_request_id':request_id,'_draft_id':draft_id,'_plan':d['after']}
            c.execute(m.image_jobs.insert().values(id=job_id,owner_id=principal.id,data=job))
            result={'job_id':job_id}
            d.update(status='committed',result=result); d.pop('_approval',None)
            self._put(c,m.drafts,draft_id,d)
            op=self._receipt(c,principal,request_id,payload,'image_generation',result,'running')
            return {'claimed':True,'operation':op,'job_id':job_id,'plan':d['after']}

    def _verified_image(self,data,mime_type):
        from PIL import Image
        if not isinstance(data,bytes) or not 0<len(data)<=20*1024*1024:
            raise AgentError('Invalid image output size',422)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width*image.height>40_000_000 or image.width<=0 or image.height<=0: raise ValueError()
                expected={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(image.format)
                if expected!=mime_type: raise ValueError()
                image.verify()
        except Exception:
            raise AgentError('Invalid image output bytes',422) from None

    def complete_image_job(self,principal,job_id,outputs,status='completed',error=None):
        if status not in ('completed','unknown','failed'): raise AgentError('Invalid terminal image status',422)
        if len(outputs)>4 or (status=='completed' and not outputs): raise AgentError('Invalid output count',422)
        for output in outputs: self._verified_image(output['data'],output['mime_type'])
        with self._write() as c:
            job=self._get(c,m.image_jobs,job_id,principal)
            op=self._get(c,m.operations,job['_request_id'],principal)
            if job['status']!='running': return public(op)
            if len(outputs)>job['_plan']['count']: raise AgentError('Provider output count exceeds approved plan',422)
            for output in outputs:
                id=uid('output'); data=output['data']
                c.execute(m.image_outputs.insert().values(id=id,owner_id=principal.id,job_id=job_id,mime_type=output['mime_type'],digest=hashlib.sha256(data).hexdigest(),size=len(data),data=data))
                job['outputs'].append({'id':id,'preview_url':f'/api/lore-agent/image-outputs/{id}'})
            job.update(status=status,error=error[:500] if error else None)
            op.update(status=status,error=job['error'])
            self._put(c,m.image_jobs,job_id,job)
            related=c.execute(select(m.operations.c.id,m.operations.c.data).where(m.operations.c.data['kind'].as_string()=='image_generation',m.operations.c.data['result']['job_id'].as_string()==job_id)).all()
            for related_id,data in related:
                data=dict(data,status=status,error=job['error'])
                self._put(c,m.operations,related_id,data)
            return public(op)

    def get_image_job(self,id,principal):
        with self.engine.connect() as c:
            self._authorize(c,principal,'lore:image:generate')
            job=self._get(c,m.image_jobs,id,principal)
            return {**public(job),'plan':{'target_kind':'character',**deepcopy(job['_plan'])}}

    def image_output(self,id,principal):
        with self.engine.connect() as c:
            self._authorize(c,principal,'lore:image:generate')
            row=c.execute(select(m.image_outputs).where(m.image_outputs.c.id==id)).mappings().first()
            if not row or (not principal.is_owner and row['owner_id']!=principal.id): raise AgentError('Not found',404)
            if row['size']!=len(row['data']) or hashlib.sha256(row['data']).hexdigest()!=row['digest']: raise AgentError('Image integrity failure',409)
            self._verified_image(row['data'],row['mime_type'])
            return {'data':row['data'],'mime_type':row['mime_type']}

    def stage_image_registration(self,principal,job_id,request_id,output_ids,target_id,metadata,session_id=None,target_kind='character'):
        value=RegistrationBody(request_id=request_id,output_ids=output_ids,target_id=target_id,target_kind=target_kind,metadata=metadata)
        allowed={'label','description','tags','mood_tags','scene_tags','outfit_tags','pose_tags','expression_tags','priority'}
        if set(metadata)-allowed: raise AgentError('Unsupported asset metadata',422)
        from app.schemas.assets import CharacterAssetCreate
        normalized=CharacterAssetCreate.model_validate({'label':'Generated image',**metadata,'image_url':'verified-output'},strict=True).model_dump()
        clean={k:normalized[k] for k in allowed}
        bounded(clean,16000)
        payload={'job_id':job_id,'session_id':session_id,**value.model_dump()}
        # Preserve old character receipt hashes for exact retries across upgrades.
        if value.target_kind=='character': payload.pop('target_kind')
        with self._write() as c:
            self._authorize(c,principal,'lore:draft')
            replay=self._replay(c,request_id,payload,principal)
            if replay: return public(self._get(c,m.drafts,replay['result']['draft_id'],principal))
            job=self._get(c,m.image_jobs,job_id,principal)
            if job['status']!='completed' or len(set(output_ids))!=len(output_ids) or not set(output_ids).issubset({x['id'] for x in job['outputs']}): raise AgentError('Invalid completed image outputs',422)
            if target_id!=job['_plan']['target_id'] or value.target_kind!=job['_plan'].get('target_kind','character'): raise AgentError('Image registration target differs from approved plan',422)
            _,revision=self._entity(c,value.target_kind,target_id)
            plan={'job_id':job_id,'output_ids':output_ids,'target_id':target_id,'target_kind':value.target_kind,'metadata':clean if value.target_kind=='character' else {},'generation_prompt':job['_plan']['prompt']}
            before=None
            if value.target_kind=='world':
                row=c.execute(select(m.image_outputs).where(m.image_outputs.c.id==output_ids[0])).mappings().first()
                if not row or row['job_id']!=job_id: raise AgentError('Output unavailable',409)
                self._verified_image(row['data'],row['mime_type'])
                if row['size']!=len(row['data']) or hashlib.sha256(row['data']).hexdigest()!=row['digest']: raise AgentError('Image integrity failure')
                ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[row['mime_type']]
                plan.update(output_digest=row['digest'],thumbnail_url='/uploads/lore-agent/'+row['digest']+'.'+ext)
                before={'thumbnail_url':c.execute(select(WorldSetting.__table__.c.thumbnail_url).where(WorldSetting.__table__.c.id==target_id)).scalar_one()}
            d=self._new_draft(c,principal,'image_registration',plan,target_id,session_id,before=before,base_revision=revision)
            self._receipt(c,principal,request_id,payload,'stage_image_registration',{'draft_id':d['id']})
            return d

    def _register_images(self,c,principal,d):
        # Persist immutable bytes under the configured managed upload root so ordinary
        # asset rendering needs no owner key. Unapproved outputs remain private DB blobs.
        from pathlib import Path
        from app.core.config import get_settings
        root=Path(get_settings().upload_root).resolve()/'lore-agent'
        root.mkdir(parents=True,exist_ok=True)
        ids=[]
        for output_id in d['after']['output_ids']:
            row=c.execute(select(m.image_outputs).where(m.image_outputs.c.id==output_id)).mappings().first()
            if not row or row['job_id']!=d['after']['job_id']: raise AgentError('Output unavailable',409)
            self._verified_image(row['data'],row['mime_type'])
            if hashlib.sha256(row['data']).hexdigest()!=row['digest']: raise AgentError('Image integrity failure')
            ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[row['mime_type']]
            name=row['digest']+'.'+ext
            is_world=d['after'].get('target_kind','character')=='world'
            if is_world and (len(d['after']['output_ids'])!=1 or row['digest']!=d['after']['output_digest'] or '/uploads/lore-agent/'+name!=d['after']['thumbnail_url']):
                raise AgentError('Approved world output changed')
            path=root/name
            # Exclusive immutable creation; validate an existing digest path.
            try:
                with path.open('xb') as stream: stream.write(row['data'])
            except FileExistsError:
                if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['digest']: raise AgentError('Asset path integrity failure')
            if is_world:
                t=WorldSetting.__table__
                changed=c.execute(t.update().where(t.c.id==d['target_id']).where(text('agent_revision=:base_revision')).values(thumbnail_url=d['after']['thumbnail_url'],updated_at=utc_now()),{'base_revision':d['base_revision']}).rowcount
                if changed!=1: raise AgentError('Entity revision conflict')
                return {'id':d['target_id'],'kind':'world','target_id':d['target_id'],'target_kind':'world','output_id':output_id,'thumbnail_url':d['after']['thumbnail_url'],'path':'/world-settings/'+d['target_id']}
            asset=CharacterAsset(id=uid('asset'),character_id=d['target_id'],**d['after']['metadata'],image_url='/uploads/lore-agent/'+name,enabled=True,is_default=False,source='lore_agent',generation_prompt=d['after']['generation_prompt'],metadata_={'agent_output_id':output_id})
            data=asset.model_dump(); data['metadata']=data.pop('metadata_')
            c.execute(CharacterAsset.__table__.insert().values(**data)); ids.append(asset.id)
        return {'asset_ids':ids,'target_id':d['target_id']}
