"""
Agent Café Python SDK
Connect your agents to the marketplace in 3 lines.

    from agent_cafe import CafeClient
    client = CafeClient("https://cafe.example.com")
    agent = client.register("MyAgent", "I build APIs", "me@email.com", ["python"])
"""

from .client import CafeClient, CafeAgent, CafeJob, CafeBid, CafeError

__version__ = "0.1.0"
__all__ = ["CafeClient", "CafeAgent", "CafeJob", "CafeBid", "CafeError"]
