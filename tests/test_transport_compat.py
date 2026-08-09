import warnings
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

@pytest.mark.asyncio
async def test_old_import_path_still_works():
    """The old mcp_vcr.transport path must continue to resolve and emit warnings on call/instantiation."""
    from mcp_vcr.transport import run_proxy, launch_server, StreamWriterWrapper
    assert callable(run_proxy)
    assert callable(launch_server)
    assert callable(StreamWriterWrapper)

    # Test StreamWriterWrapper triggers warning on instantiation
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        raw = MagicMock()
        wrapper = StreamWriterWrapper(raw)
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "StreamWriterWrapper is deprecated" in str(w[0].message)

    # Test launch_server triggers warning on call
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        with patch("mcp_vcr.transports.stdio.launch_server", new_callable=AsyncMock) as mock_launch:
            await launch_server(["python"])
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "launch_server is deprecated" in str(w[0].message)
