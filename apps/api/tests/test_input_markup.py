from app.engine.input_markup import parse_babechat_input_markup


def test_parse_babechat_input_markup_splits_asterisk_action_and_dialogue():
    parsed = parse_babechat_input_markup('*아리아 옆에 조용히 앉는다* 오늘 좀 지쳤어')

    assert parsed.had_markup is True
    assert parsed.action == '아리아 옆에 조용히 앉는다'
    assert parsed.dialogue == '오늘 좀 지쳤어'
    assert [(part.type, part.text) for part in parsed.parts] == [
        ('action', '아리아 옆에 조용히 앉는다'),
        ('dialogue', '오늘 좀 지쳤어'),
    ]


def test_parse_babechat_input_markup_combines_multiple_action_segments():
    parsed = parse_babechat_input_markup('*문을 닫는다* 있잖아 *시선을 피한다* 잠깐 얘기해')

    assert parsed.action == '문을 닫는다 시선을 피한다'
    assert parsed.dialogue == '있잖아 잠깐 얘기해'
    assert [(part.type, part.text) for part in parsed.parts] == [
        ('action', '문을 닫는다'),
        ('dialogue', '있잖아'),
        ('action', '시선을 피한다'),
        ('dialogue', '잠깐 얘기해'),
    ]


def test_parse_babechat_input_markup_keeps_unclosed_asterisk_as_dialogue():
    parsed = parse_babechat_input_markup('음 *오늘은 그냥 쉬자')

    assert parsed.had_markup is False
    assert parsed.action == ''
    assert parsed.dialogue == '음 *오늘은 그냥 쉬자'


def test_parse_babechat_input_markup_allows_action_only():
    parsed = parse_babechat_input_markup('*조용히 손을 잡는다*')

    assert parsed.had_markup is True
    assert parsed.action == '조용히 손을 잡는다'
    assert parsed.dialogue == ''


def test_parse_babechat_input_markup_keeps_double_asterisk_bold_as_dialogue():
    parsed = parse_babechat_input_markup('**굵은 말** 이건 대사')

    assert parsed.had_markup is False
    assert parsed.action == ''
    assert parsed.dialogue == '**굵은 말** 이건 대사'


def test_parse_babechat_input_markup_treats_triple_dot_as_dialogue():
    parsed = parse_babechat_input_markup('...문을 닫는다... 안녕')

    assert parsed.had_markup is False
    assert parsed.action == ''
    assert parsed.dialogue == '...문을 닫는다... 안녕'
