from modelbridge.schemas import ChatMessage
from modelbridge.context.windows import estimate_message_tokens


def test_estimate_message_tokens_handles_list_content():
    m = ChatMessage(role="user", content=[
        {"type": "text", "text": "描述这张图片里的内容"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ])
    # 不抛异常；按文本块估算 > 0
    assert estimate_message_tokens(m) > 0
