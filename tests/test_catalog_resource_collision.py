"""Tests for resource URI collision detection in Catalog.add_server().

Bug: resources were appended without checking for duplicate URIs, unlike tools
and prompts which log a warning and skip duplicates. This caused silent data
loss when two servers declared the same resource URI.
"""

from modelbridge.mcp.manager.catalog import Catalog
from modelbridge.mcp.protocol.types import MCPResource, MCPTool


def _make_resource(uri: str, name: str = "") -> MCPResource:
    return MCPResource(uri=uri, name=name or uri)


class TestServerIdPrefixCollision:
    """Two distinct server ids that sanitise to the same qualified-name prefix
    (``foo.bar`` / ``foo/bar`` → ``foo_bar``) must not silently shadow each
    other's tools or misroute calls — the second one is refused."""

    def test_colliding_server_ids_second_is_refused(self):
        cat = Catalog()
        t = MCPTool(name="search")
        cat.add_server("foo.bar", tools=[t], resources=[], prompts=[])
        cat.add_server("foo/bar", tools=[t], resources=[], prompts=[])

        assert len(cat.tools) == 1
        assert cat.tools[0].server_id == "foo.bar"

    def test_colliding_prefix_resolves_to_first_server(self):
        cat = Catalog()
        cat.add_server("foo.bar", tools=[MCPTool(name="search")], resources=[], prompts=[])
        cat.add_server("foo/bar", tools=[MCPTool(name="search")], resources=[], prompts=[])

        resolved = cat.resolve_tool("foo_bar__search")
        assert resolved is not None
        assert resolved[0] == "foo.bar"

    def test_distinct_prefixes_both_kept(self):
        cat = Catalog()
        cat.add_server("alpha", tools=[MCPTool(name="search")], resources=[], prompts=[])
        cat.add_server("beta", tools=[MCPTool(name="search")], resources=[], prompts=[])
        assert len(cat.tools) == 2

    def test_re_adding_same_server_id_is_allowed(self):
        # Same id (e.g. after remove_server during a hot refresh) must not be
        # treated as a collision.
        cat = Catalog()
        cat.add_server("alpha", tools=[MCPTool(name="search")], resources=[], prompts=[])
        cat.remove_server("alpha")
        cat.add_server("alpha", tools=[MCPTool(name="search")], resources=[], prompts=[])
        assert len(cat.tools) == 1
        assert cat.tools[0].server_id == "alpha"


class TestResourceCollisionDetection:
    def test_duplicate_uri_second_is_skipped(self):
        """Two add_server calls with the same resource URI → only the first survives."""
        cat = Catalog()
        res = _make_resource("file:///shared/resource.txt")

        cat.add_server("s1", tools=[], resources=[res], prompts=[])
        cat.add_server("s2", tools=[], resources=[res], prompts=[])

        assert len(cat.resources) == 1, (
            f"Expected 1 resource (duplicate skipped), got {len(cat.resources)}"
        )

    def test_duplicate_uri_first_server_wins(self):
        """When URIs collide, the entry from the FIRST server is kept."""
        cat = Catalog()
        res = _make_resource("file:///shared/resource.txt")

        cat.add_server("s1", tools=[], resources=[res], prompts=[])
        cat.add_server("s2", tools=[], resources=[res], prompts=[])

        assert cat.resources[0].server_id == "s1", (
            f"Expected server_id 's1', got '{cat.resources[0].server_id}'"
        )

    def test_distinct_uris_both_kept(self):
        """Two servers with different URIs → both resources are kept."""
        cat = Catalog()
        res_a = _make_resource("file:///server1/data.txt")
        res_b = _make_resource("file:///server2/data.txt")

        cat.add_server("s1", tools=[], resources=[res_a], prompts=[])
        cat.add_server("s2", tools=[], resources=[res_b], prompts=[])

        assert len(cat.resources) == 2, (
            f"Expected 2 resources (distinct URIs), got {len(cat.resources)}"
        )

    def test_find_resource_returns_first_after_dedup(self):
        """find_resource() returns the surviving entry after collision dedup."""
        cat = Catalog()
        res = _make_resource("proto://host/path")

        cat.add_server("s1", tools=[], resources=[res], prompts=[])
        cat.add_server("s2", tools=[], resources=[res], prompts=[])

        found = cat.find_resource("proto://host/path")
        assert found is not None
        assert found.server_id == "s1"

    def test_warning_logged_on_collision(self):
        """A warning is emitted when a duplicate resource URI is detected."""
        from unittest.mock import MagicMock, patch

        cat = Catalog()
        res = _make_resource("urn:test:collision")

        mock_logger = MagicMock()
        with patch("modelbridge.mcp.manager.catalog.mcp_logger", return_value=mock_logger):
            cat.add_server("s1", tools=[], resources=[res], prompts=[])
            cat.add_server("s2", tools=[], resources=[res], prompts=[])

        # Verify warning() was called and the message mentions collision
        assert mock_logger.warning.called, "Expected logger.warning() to be called"
        call_args = mock_logger.warning.call_args
        assert "collision" in call_args[0][0], (
            f"Expected 'collision' in warning format string, got: {call_args}"
        )
