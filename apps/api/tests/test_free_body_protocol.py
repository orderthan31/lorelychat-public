import json
import pytest
from app.db.models import Character
from app.engine.free_body_protocol import FreeBodyError, speaker_aliases, parse_response, parse_body


def cast():
    return [Character(id='one', name='A'), Character(id='two', name='B')]


@pytest.mark.parametrize('protocol', ['free_body_json', 'tagged_text'])
def test_order_and_full_text(protocol):
    body = '<a>movement\ncontinued</a>Hello<t>' + 'private ' * 80 + '</t>Again'
    source = json.dumps({'replies': [{'who': 'C1', 'text': body}]}) if protocol == 'free_body_json' else f'<m c="C1">{body}</m>'
    reply = parse_response(source, protocol=protocol, aliases=speaker_aliases(cast()))[0]
    assert reply.raw_body == body
    assert [p['type'] for p in reply.render_parts] == ['action', 'dialogue', 'thought', 'dialogue']
    assert reply.text == 'Hello\nAgain'
    assert reply.thought == 'private ' * 80
    assert 'raw_body' not in reply.model_dump()


def test_surface_variants_and_code_literals():
    response = "```xml\n<M C = 'c1' E='calm'>`<m c=\"BAD\">literal</m>`\n<a>moves</a>(not thought) *not action*</M>\n```"
    reply = parse_response(response, protocol='tagged_text', aliases=speaker_aliases(cast()))[0]
    assert reply.emotion == 'calm'
    assert reply.thought is None
    assert '<m c="BAD">literal</m>' in reply.text
    assert reply.action == 'moves'


@pytest.mark.parametrize('source', [
    '<m c="BAD">x</m>', '<m c="C1">x<m c="C2">y</m></m>',
    '<m c="C1">x', '<m c="C1">x</m>junk',
    '<m c="C1" c="C2">x</m>', '<m c="C1"><a>x<t>y</t></a></m>',
    '<m c="C1"><t>x</m>', '<m c="N">only narration</m>',
    '<m c="C1">ok</m>`trailing discarded code`',
    '<m c="C1\'>mismatched quotes</m>',
])
def test_reject_atomically(source):
    with pytest.raises(FreeBodyError):
        parse_response(source, protocol='tagged_text', aliases=speaker_aliases(cast()))


def test_action_only_and_private_only_allowed():
    for body in ['<a>waits</a>', '<t>private</t>']:
        reply = parse_response(f'<m c="C1">{body}</m>', protocol='tagged_text', aliases=speaker_aliases(cast()))[0]
        assert reply.text == ''


def test_silent_identity_and_plain_fallback():
    aliases = speaker_aliases(cast(), {'one': 'silent'})
    assert aliases == {'C1': 'two', 'N': None}
    assert parse_response('Hello (really).', protocol='tagged_text', aliases=aliases)[0].character_id == 'two'
    with pytest.raises(FreeBodyError):
        parse_response('A: Hello', protocol='tagged_text', aliases=aliases)
    with pytest.raises(FreeBodyError):
        parse_response('Hello', protocol='tagged_text', aliases=speaker_aliases(cast()))


@pytest.mark.parametrize('source', ['{"replies":[{"who":"C1","who":"C2","text":"x"}]}', '{"replies":[{"who":"C1","text":"ok"},null]}'])
def test_json_ambiguity_rejected(source):
    with pytest.raises(FreeBodyError):
        parse_response(source, protocol='free_body_json', aliases=speaker_aliases(cast()))


def test_empty_and_literal_html():
    assert parse_body('<script>alert(1)</script>')[0]['text'] == '<script>alert(1)</script>'
    with pytest.raises(FreeBodyError):
        parse_body('<a> </a><t></t>')


def test_smart_quotes_json_case_multiline_code_and_narrator():
    body = 'A code sample:\n```xml\n<m c="UNKNOWN"><t>literal</t></m>\n```\nStill spoken.'
    source = f'<M C=“c1” E=‘calm’>{body}</M><m c="N">Rain falls.</m>'
    replies = parse_response(source, protocol='tagged_text', aliases=speaker_aliases(cast()))
    assert replies[0].raw_body == body
    assert replies[0].thought is None and replies[0].text == body
    assert replies[1].reply_type == 'storytelling' and replies[1].character_id is None
    json_source = json.dumps({'REPLIES': [{'WHO': 'c1', 'TEXT': body}]})
    assert parse_response(json_source, protocol='free_body_json', aliases=speaker_aliases(cast()))[0].text == body


def test_all_silent_cast_and_abuse_bounds():
    aliases = speaker_aliases(cast(), {'one': 'silent', 'two': 'silent'})
    with pytest.raises(FreeBodyError):
        parse_response('<m c="C1">No.</m>', protocol='tagged_text', aliases=aliases)
    with pytest.raises(FreeBodyError):
        parse_response('<m c="C1">x</m>' * 33, protocol='tagged_text', aliases=speaker_aliases(cast()))
    with pytest.raises(FreeBodyError):
        parse_body('x' * 100001)
    # Long delimiter runs must not trigger regex backtracking or text clipping.
    body = '`' * 90000
    assert parse_body(body) == [{'type': 'dialogue', 'text': body}]


def test_no_semantic_invention_or_identity_header_guessing():
    parts = parse_body('Ordinary (inner?) **italic emphasis** and \\<a>literal\\</a>.')
    assert len(parts) == 1 and parts[0]['type'] == 'dialogue'
    with pytest.raises(FreeBodyError):
        parse_response('Hello.\nUnknown: speaks.', protocol='tagged_text', aliases={'C1': 'one', 'N': None})
