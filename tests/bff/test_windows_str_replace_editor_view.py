import pytest

from app.tool.file_operators import LocalFileOperator
from app.tool.str_replace_editor import StrReplaceEditor


def test_local_file_operator_decode_output_gbk_fallback():
    text = "参数格式不正确"
    payload = text.encode("gbk")
    assert LocalFileOperator._decode_output(payload) == text


@pytest.mark.asyncio
async def test_view_directory_works_with_local_operator(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.txt").write_text("y", encoding="utf-8")

    result = await StrReplaceEditor._view_directory(str(tmp_path), LocalFileOperator())
    output = str(result)

    assert "up to 2 levels deep" in output
    assert "b.txt" in output
    assert ".hidden" not in output
