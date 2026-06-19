from modelbridge.agent.mentions import resolve_mentions, collect_image_blocks, build_injection_messages
from modelbridge.project.file_index import FileIndex, FileEntry


def _index(root, *relpaths):
    return FileIndex(root=root, entries=[FileEntry(relpath=r, is_dir=False) for r in relpaths])


def test_image_mention_yields_image_attachment(tmp_path):
    img = tmp_path / "diagram.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    idx = _index(tmp_path, "diagram.png")
    res = resolve_mentions("@diagram.png 这是什么", idx, project_root=tmp_path)
    atts = [a for a in res.attachments if a.kind == "image"]
    assert len(atts) == 1
    assert atts[0].block["type"] == "image_url"


def test_text_mention_still_text(tmp_path):
    code = tmp_path / "a.py"
    code.write_text("print(1)", encoding="utf-8")
    idx = _index(tmp_path, "a.py")
    res = resolve_mentions("@a.py", idx, project_root=tmp_path)
    assert res.attachments and res.attachments[0].kind == "file"


def test_paste_pseudo_mention(monkeypatch, tmp_path):
    import modelbridge.agent.mentions as M
    fake = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    monkeypatch.setattr(M.images, "block_from_clipboard", lambda: fake)
    res = resolve_mentions("@paste 看这个", _index(tmp_path), project_root=tmp_path)
    assert any(a.kind == "image" and a.block == fake for a in res.attachments)


def test_url_image_collected(tmp_path):
    res = resolve_mentions("看 https://x.com/pic.png 这个", _index(tmp_path), project_root=tmp_path)
    blocks = collect_image_blocks(res)
    assert any(b["image_url"]["url"] == "https://x.com/pic.png" for b in blocks)


def test_collect_image_blocks_from_file_mention(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    res = resolve_mentions("@x.png hi", _index(tmp_path, "x.png"), project_root=tmp_path)
    blocks = collect_image_blocks(res)
    assert blocks and blocks[0]["type"] == "image_url"


def test_build_injection_skips_image_attachments(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    res = resolve_mentions("@x.png hi", _index(tmp_path, "x.png"), project_root=tmp_path)
    # 图像附件不进文本注入消息
    assert build_injection_messages(res) == []
