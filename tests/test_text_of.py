from modelbridge.schemas import ChatMessage, text_of


def test_text_of_str_passthrough():
    assert text_of("hello") == "hello"


def test_text_of_none_is_empty():
    assert text_of(None) == ""


def test_text_of_list_joins_text_blocks_ignores_images():
    content = [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "是什么"},
    ]
    assert text_of(content) == "看这张图 是什么"


def test_chatmessage_accepts_list_content_and_to_wire_passes_through():
    blocks = [
        {"type": "text", "text": "q"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    m = ChatMessage(role="user", content=blocks)
    wire = m.to_wire()
    assert wire["content"] == blocks  # 原样透传，未被字符串化
