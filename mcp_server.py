"""MCP (Model Context Protocol) Server for extensible agent tools."""

import json
from typing import Any, Dict, List, Callable
import structlog

logger = structlog.get_logger(__name__)


class MCPTool:
    """Represents a single tool in the MCP system."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable,
        category: str = "general",
        version: str = "1.0.0",
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.category = category
        self.version = version

    def to_openai_format(self) -> Dict[str, Any]:
        """Convert tool to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": [
                        name
                        for name, prop in self.parameters.items()
                        if prop.get("required", True)
                    ],
                },
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool with given arguments."""
        try:
            result = await self.handler(**kwargs)
            return {
                "status": "success",
                "tool": self.name,
                "result": result,
            }
        except Exception as e:
            logger.error("Tool execution failed", tool=self.name, error=str(e))
            return {
                "status": "error",
                "tool": self.name,
                "error": str(e),
            }


class MCPServer:
    """Server managing and executing MCP tools."""

    def __init__(self):
        self.tools: Dict[str, MCPTool] = {}
        self.categories: Dict[str, List[str]] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable,
        category: str = "general",
    ) -> None:
        """Register a new tool."""
        tool = MCPTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            category=category,
        )

        self.tools[name] = tool

        # Track by category
        if category not in self.categories:
            self.categories[category] = []
        self.categories[category].append(name)

        logger.info(
            "Tool registered",
            tool_name=name,
            category=category,
            parameters=list(parameters.keys()),
        )

    def unregister_tool(self, name: str) -> bool:
        """Unregister a tool."""
        if name in self.tools:
            tool = self.tools.pop(name)
            if tool.category in self.categories:
                self.categories[tool.category].remove(name)
            logger.info("Tool unregistered", tool_name=name)
            return True
        return False

    def get_tool(self, name: str) -> MCPTool:
        """Get a tool by name."""
        return self.tools.get(name)

    def list_tools(self, category: str = None) -> List[Dict[str, Any]]:
        """List all available tools, optionally filtered by category."""
        tools_to_list = []

        if category:
            tool_names = self.categories.get(category, [])
            tools_to_list = [
                {
                    "name": name,
                    "description": self.tools[name].description,
                    "category": category,
                }
                for name in tool_names
            ]
        else:
            tools_to_list = [
                {
                    "name": name,
                    "description": tool.description,
                    "category": tool.category,
                }
                for name, tool in self.tools.items()
            ]

        return tools_to_list

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Get all tools in OpenAI function calling format."""
        return [tool.to_openai_format() for tool in self.tools.values()]

    def get_tool_specs(self) -> Dict[str, Any]:
        """Get detailed specifications for all tools."""
        return {
            name: {
                "description": tool.description,
                "parameters": tool.parameters,
                "category": tool.category,
                "version": tool.version,
            }
            for name, tool in self.tools.items()
        }

    async def execute_tool(self, name: str, input_params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool by name with given parameters."""
        tool = self.get_tool(name)
        if not tool:
            logger.warning("Tool not found", tool_name=name)
            return {
                "status": "error",
                "tool": name,
                "error": f"Tool '{name}' not found",
            }

        logger.info("Executing tool", tool_name=name)
        return await tool.execute(**input_params)

    def to_json(self) -> str:
        """Serialize server state to JSON."""
        return json.dumps(
            {
                "tools": self.get_tool_specs(),
                "categories": self.categories,
                "total_tools": len(self.tools),
            },
            indent=2,
        )


# Global MCP server instance
mcp_server = MCPServer()


async def initialize_default_tools(
    tools_executor,
) -> None:
    """Initialize default customer success tools."""

    # Register search_knowledge_base tool
    mcp_server.register_tool(
        name="search_knowledge_base",
        description="Search the knowledge base for relevant articles and documentation",
        parameters={
            "query": {
                "type": "string",
                "description": "Search query string",
                "required": True,
            },
            "category": {
                "type": "string",
                "description": "Optional category filter (onboarding, technical, billing, etc.)",
                "required": False,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5)",
                "required": False,
            },
        },
        handler=tools_executor.search_knowledge_base,
        category="knowledge",
    )

    # Register create_ticket tool
    mcp_server.register_tool(
        name="create_ticket",
        description="Create a new support ticket for tracking and resolution",
        parameters={
            "customer_email": {
                "type": "string",
                "description": "Customer email address",
                "required": True,
            },
            "subject": {
                "type": "string",
                "description": "Ticket subject/title",
                "required": True,
            },
            "priority": {
                "type": "string",
                "description": "Priority level: low, medium, high, critical",
                "required": False,
            },
            "category": {
                "type": "string",
                "description": "Ticket category: onboarding, technical, billing, general",
                "required": False,
            },
            "customer_name": {
                "type": "string",
                "description": "Customer name for personalization",
                "required": False,
            },
        },
        handler=tools_executor.create_ticket,
        category="ticket_management",
    )

    # Register get_customer_history tool
    mcp_server.register_tool(
        name="get_customer_history",
        description="Retrieve customer's past tickets and interaction history",
        parameters={
            "customer_email": {
                "type": "string",
                "description": "Customer email address",
                "required": True,
            },
            "limit": {
                "type": "integer",
                "description": "Number of past tickets to retrieve (default: 10)",
                "required": False,
            },
            "include_resolved": {
                "type": "boolean",
                "description": "Whether to include resolved tickets (default: true)",
                "required": False,
            },
        },
        handler=tools_executor.get_customer_history,
        category="customer_insight",
    )

    # Register escalate_to_human tool
    mcp_server.register_tool(
        name="escalate_to_human",
        description="Escalate complex issue to human agent for manual handling",
        parameters={
            "ticket_id": {
                "type": "string",
                "description": "UUID of the ticket to escalate",
                "required": True,
            },
            "reason": {
                "type": "string",
                "description": "Reason for escalation",
                "required": True,
            },
            "priority_level": {
                "type": "string",
                "description": "Priority level: low, medium, high, critical (default: high)",
                "required": False,
            },
        },
        handler=tools_executor.escalate_to_human,
        category="escalation",
    )

    # Register send_response tool
    mcp_server.register_tool(
        name="send_response",
        description="Send a response message to customer via specified channel",
        parameters={
            "ticket_id": {
                "type": "string",
                "description": "UUID of the ticket",
                "required": True,
            },
            "message": {
                "type": "string",
                "description": "Response message to send",
                "required": True,
            },
            "channel": {
                "type": "string",
                "description": "Channel: email, whatsapp, webform (default: email)",
                "required": False,
            },
        },
        handler=tools_executor.send_response,
        category="communication",
    )

    logger.info("Default tools initialized", tools_count=len(mcp_server.tools))
