from modelbridge.prompt.builder import PromptBuilder


def test_user_request_with_images_builds_list_content():
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    result = PromptBuilder().with_user_request("这是什么", images=[img]).build()
    user = result.messages[-1]
    assert user.role == "user"
    assert isinstance(user.content, list)
    assert user.content[0] == {"type": "text", "text": "这是什么"}
    assert user.content[1] == img


def test_user_request_without_images_stays_string():
    result = PromptBuilder().with_user_request("纯文本").build()
    assert result.messages[-1].content == "纯文本"


def test_images_do_not_change_stable_prefix_hash():
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    a = PromptBuilder().with_system_prompt("S").with_user_request("q").build()
    b = PromptBuilder().with_system_prompt("S").with_user_request("q", images=[img]).build()
    assert a.stable_prefix_hash == b.stable_prefix_hash
