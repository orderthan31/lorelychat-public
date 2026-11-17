"""Authored genre policy keeps conversation/world callers compatible."""
import pytest

from app.services.genre_domain_service import normalize_genre_mode


@pytest.mark.parametrize(('value', 'expected'), [
    (None, 'battle'),
    ('', 'battle'),
    ('unknown', 'battle'),
    (' BATTLE ', 'battle'),
    ('Romance', 'romance'),
    ('slice-of-life', 'slice_of_life'),
    ('fantasy', 'fantasy'),
    ('mystery', 'mystery'),
    ('custom', 'custom'),
])
def test_authored_genre_normalization(value, expected):
    assert normalize_genre_mode(value) == expected
