import pytest
from src.services.file_manager import sanitize_filename


@pytest.mark.parametrize(
    "raw_name,fallback,expected",
    [
        ("report.pdf", "fallback_id", "report.pdf"),
        ("../../etc/passwd", "fallback_id", "passwd"),
        ("..\\..\\windows\\system32\\calc.exe", "fallback_id", "calc.exe"),
        ("dir/subdir/nested.txt", "fallback_id", "nested.txt"),
        ("../../../", "fallback_id", "fallback_id"),
        ("", "fallback_id", "fallback_id"),
        ("   ", "fallback_id", "fallback_id"),
        ("...hidden.png", "fallback_id", "hidden.png"),
        ("bad\x00file.png", "fallback_id", "badfile.png"),
        ("normal_file_123.tar.gz", "fallback_id", "normal_file_123.tar.gz"),
    ],
)
def test_sanitize_filename(raw_name, fallback, expected):
    result = sanitize_filename(raw_name, fallback)
    assert result == expected
