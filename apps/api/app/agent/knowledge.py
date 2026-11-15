"""Bounded, source-owned product help, not library content or a schema dump.

Sources: contracts.py; schemas/{characters,world_settings}.py;
services/{conversation,world_setting}_service.py; engine/prompts.py;
web WorldSettings editor. Tests pin this help to the authored allowlists.
"""
from copy import deepcopy

from app.agent.contracts import CHARACTER_FIELDS, WORLD_FIELDS

PRODUCT_PRIMER = """Lorechat product and draft workflow:
Lorechat is a character roleplay/chat app. Characters are reusable authored
personas, worlds are reusable setting/scene templates, and conversations use
characters with an OPTIONAL world. A world draft does not create a conversation
or attach participants. These tools cannot create rooms or edit user personas,
chat history, provider settings, TTS or default avatars. Agent LLM and image
settings are independent of roleplay/compression/Decision/TTS model settings.

For a genuine create/edit request, turn coherent requirements into an app-native
APPROVABLE DRAFT using lore_stage_character or lore_stage_world, then inspect the
returned normalized preview and use lore_request_approval. Do not stop at a
creative lecture, a capabilities listing, or a generic multi-question intake.
Advice-only or brainstorming questions do not require a write. Ask only when a
material ambiguity or missing choice prevents a useful draft (especially which
existing target to edit). For minor creative gaps, propose reversible assumptions
in the draft and explicitly label them in your reply; never present them as user
facts. Do not apply this discretion to approval, image mode/reference or models.
Preserve the user's specific prose, rules, relationships and exceptions rather
than replacing them with a short generic summary. Answer in the user's language.

World description is a library-facing overview, NOT a substitute for world_seed:
put persistent canon, setting rules and origin premise in world_seed; the initial
situation in opening_scene; the optional first character utterance in opening_line.
opening_line can become the first character's actual message when a room is made.
Keep detailed canon in world_seed even when description is concise. Characters use
persona for core identity/background/motivations, appearance for visual details,
behavior_style and speech_style for conduct/voice, and list-valued emotional_rules
and forbidden_rules for reactions and boundaries. trait_scores are 1..5 integers,
not a replacement for prose. Use only the allowed fields described by the tools.

Create: omit target_id (or null), supply required name+persona or title. You need
not search unrelated library content for an original creation. Edit: resolve an
actual target via bounded lore_search and task-relevant lore_get_* fields; pass
its id as target_id and only changed fields. Preserve unspecified fields and
existing detail. Use lore_capabilities when field/workflow help is needed, not as
a mandatory first step. Revisions use lore_revise_draft with the current version;
inspect the new preview and request review again because old approval is invalid.
Summarize the proposal by its character/world name, key changes and assumptions
as pending review in the corresponding editor, not saved. Keep draft/session ids,
field keys and tool plumbing out of normal replies unless the user requests them.
Only an exact owner-approved version/hash may be committed; only a
successful operation receipt proves saving. A review request is not approval.
Images require a separate approved plan and separate approved registration;
generated outputs do not automatically become avatars or world thumbnails.
"""

FIELD_HELP = {
    'character': {
        'name': 'Required nonblank string on create; character display name.',
        'persona': 'Required nonblank string on create; core identity, background, motivations and relationships. Preserve authored detail.',
        'description': 'Optional string; character overview, separate from the core persona.',
        'appearance': 'Optional string; physical appearance and visual details, not an image or avatar change.',
        'behavior_style': 'Optional string; habitual conduct and action style.',
        'speech_style': 'Optional string; voice, diction and dialogue style.',
        'emotional_rules': 'List of strings; emotional reactions and their conditions.',
        'forbidden_rules': 'List of strings; behavioral boundaries. Preserve existing restrictions on edits.',
        'trait_scores': 'Object of integer scores 1..5. Standard keys: confidence, kindness, jealousy, eros, aggression, playfulness, shyness, initiative. Omit unless useful; defaults fill missing keys.',
    },
    'world': {
        'title': 'Required nonblank string on create, at most 120 characters; library title.',
        'description': 'Optional string; library-facing overview. Do not put the only copy of persistent canon here.',
        'genre_mode': 'String: battle, romance, fantasy, slice_of_life, mystery, custom. Default battle; choose a fitting mode explicitly. Unknown values normalize to battle.',
        'location': 'Optional string; initial scene location.',
        'mood': 'Optional string; initial scene atmosphere.',
        'world_seed': 'Optional string; persistent world canon, rules, origin premise and constraints. Retain detailed user prose here, distinct from the opening hook.',
        'opening_scene': 'Optional string; initial situation/hook, not permanent world rules or continuing history.',
        'opening_line': 'Optional string; first character utterance/beat, inserted as a character message on room creation when a character is present. Not a narrator summary.',
        'tone_preset': 'Optional string; room tone guidance for pacing, resistance and emotional payoff.',
        'relationship_archetype': 'Optional string; initial relationship pattern and guidance.',
        'compression_focus': 'Optional string; clues, relationship changes and scene anchors to retain during history compression, not a model setting.',
        'tags': 'List of strings; library tags. Blank entries are removed.',
        'enabled': 'Boolean; true by default. Disabled worlds are excluded from the default world list, not deleted.',
    },
}


def field_help(kind):
    """Never advertise fields outside the same allowlists enforced by the service."""
    allowed = CHARACTER_FIELDS if kind == 'character' else WORLD_FIELDS
    return {name: FIELD_HELP[kind][name] for name in sorted(allowed)}


def stage_help(kind):
    fields = '; '.join(f'{name}: {help}' for name, help in field_help(kind).items())
    return (f'Stage a {kind} create/update APPROVABLE DRAFT, not a saved resource. '
            'For creation omit target_id (or null); for an edit first read the resolved target, '
            'then pass target_id and only changed fields. fields is an object, not prose. '
            'Preserve user detail; label reversible creative assumptions in your reply. '
            'Inspect the returned normalized before/after, id, version and preview_hash; '
            'then lore_request_approval. No owner approval is granted here. Allowed fields: ' + fields)


def product_help():
    # Return independent data so callers cannot mutate help for future turns.
    return {'product_guide': PRODUCT_PRIMER,
            'field_help': deepcopy({kind: field_help(kind) for kind in ('character', 'world')})}
