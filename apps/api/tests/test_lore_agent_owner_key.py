from pathlib import Path
import hashlib
import stat

import pytest


def test_owner_key_is_private_and_never_printed(tmp_path, capsys):
    from app.agent.owner_key import initialize_owner_key
    target = tmp_path / 'private' / 'owner.key'
    digest = initialize_owner_key(target)
    value = target.read_text().strip()
    assert len(value) >= 40
    assert digest == hashlib.sha256(value.encode()).hexdigest()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert value not in capsys.readouterr().out


def test_existing_owner_key_is_not_overwritten(tmp_path):
    from app.agent.owner_key import initialize_owner_key
    target = tmp_path / 'owner.key'
    target.write_text('existing-user-key')
    with pytest.raises(FileExistsError):
        initialize_owner_key(target)
    assert target.read_text() == 'existing-user-key'


def test_owner_key_symlink_is_not_followed(tmp_path):
    from app.agent.owner_key import initialize_owner_key
    original = tmp_path / 'original'
    original.write_text('preserve')
    target = tmp_path / 'owner.key'
    target.symlink_to(original)
    with pytest.raises(FileExistsError):
        initialize_owner_key(target)
    assert original.read_text() == 'preserve'


def test_cli_prints_only_digest_and_setup_instructions(tmp_path, capsys):
    from app.agent.owner_key import main
    target = tmp_path / 'private' / 'owner.key'
    assert main(['init', '--out', str(target)]) == 0
    output = capsys.readouterr().out
    key = target.read_text().strip()
    assert key not in output
    assert f'LORE_AGENT_OWNER_KEY_SHA256={hashlib.sha256(key.encode()).hexdigest()}' in output
    assert str(target) in output


def test_cli_existing_file_reports_safe_failure(tmp_path, capsys):
    from app.agent.owner_key import main
    target = tmp_path / 'owner.key'
    target.write_text('existing-user-key')
    assert main(['init', '--out', str(target)]) == 1
    assert 'existing-user-key' not in capsys.readouterr().err
