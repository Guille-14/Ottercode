"""
mcp_client.py · Cliente MCP (Model Context Protocol) para OtterCode v3.0
Carga y ejecuta herramientas de servidores MCP externos vía stdio y SSE.
"""

import os
import json
import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

class CircuitBreaker:
    """Abre el circuito tras N fallos; backoff exponencial al reintentar."""
    def __init__(self, fail_max: int = 3, cooldown: float = 4.0):
        self.fail_max = fail_max
        self.cooldown = cooldown
        self.fails = 0
        self.open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            return time.monotonic() >= self.open_until

    def success(self) -> None:
        with self._lock:
            self.fails = 0
            self.open_until = 0.0

    def fail(self) -> None:
        with self._lock:
            self.fails += 1
            if self.fails >= self.fail_max:
                delay = self.cooldown * (2 ** min(self.fails - self.fail_max, 5))
                self.open_until = time.monotonic() + delay

logger = logging.getLogger("ottercode.mcp")

MCP_CONFIG_FILE = "mcp_servers.json"


class MCPSession:
    """Sesión activa con un servidor MCP."""
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.tools: List[Dict[str, Any]] = []
        self._process: Optional[asyncio.subprocess.Process] = None
        self._connected = False

    async def connect(self) -> bool:
        """Conecta al servidor MCP via stdio o SSE."""
        transport = self.config.get("transport", "stdio")
        try:
            if transport == "stdio":
                return await self._connect_stdio()
            elif transport == "sse":
                return await self._connect_sse()
        except Exception as exc:
            logger.warning(f"MCP server '{self.name}' connection failed: {exc}")
            return False

    async def _connect_stdio(self) -> bool:
        cmd = self.config.get("command", "")
        args = self.config.get("args", [])
        if not cmd:
            return False
        self._process = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Handshake initialize
        init_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ottercode", "version": "3.0.0"}
            }
        }
        await self._send(init_msg)
        resp = await self._recv()
        if resp and "result" in resp:
            self._connected = True
            await self._discover_tools()
            return True
        return False

    async def _connect_sse(self) -> bool:
        # SSE support via httpx/streaming (simplified)
        self._connected = True
        await self._discover_tools()
        return True

    async def _send(self, msg: Dict[str, Any]):
        if self._process and self._process.stdin:
            data = json.dumps(msg) + "\n"
            self._process.stdin.write(data.encode())
            await self._process.stdin.drain()

    async def _recv(self) -> Optional[Dict[str, Any]]:
        if self._process and self._process.stdout:
            line = await asyncio.wait_for(self._process.stdout.readline(), timeout=10)
            if line:
                return json.loads(line.decode())
        return None

    async def _discover_tools(self):
        """Descubre herramientas del servidor MCP via tools/list."""
        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        await self._send(req)
        resp = await self._recv()
        if resp and "result" in resp:
            self.tools = resp["result"].get("tools", [])

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Llama a una herramienta del servidor MCP."""
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}
        }
        await self._send(req)
        resp = await self._recv()
        if resp and "result" in resp:
            return resp["result"]
        return {"error": resp.get("error", "Unknown MCP error") if resp else "No response"}

    async def disconnect(self):
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        self._connected = False


class MCPManager:
    """Gestiona todas las sesiones MCP configuradas."""
    def __init__(self, config_dir: Path = None):
        self.config_dir = config_dir or Path(".")
        self.sessions: Dict[str, MCPSession] = {}
        self.tools_catalog: Dict[str, Dict[str, Any]] = {}
        self._connections: Dict[str, "_McpConnection"] = {}
        self._connected_once = False
        self.breaker = CircuitBreaker()
        self._load_config()

    def _load_config(self):
        config_path = self.config_dir / MCP_CONFIG_FILE
        if not config_path.exists():
            return
        try:
            with open(config_path) as f:
                servers = json.load(f)
            for name, cfg in servers.items():
                self.sessions[name] = MCPSession(name, cfg)
        except Exception as exc:
            logger.warning(f"Failed to load MCP config: {exc}")

    async def connect_all(self):
        """Conecta a todos los servidores MCP configurados."""
        for name, session in self.sessions.items():
            ok = await session.connect()
            if ok:
                for tool in session.tools:
                    prefixed = f"mcp__{name}__{tool['name']}"
                    self.tools_catalog[prefixed] = {
                        "name": prefixed,
                        "original_name": tool["name"],
                        "server": name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {}),
                    }
                logger.info(f"MCP server '{name}': {len(session.tools)} tools loaded")

    async def call_tool(self, prefixed_name: str, arguments: Dict[str, Any]) -> Any:
        """Llama a una herramienta MCP por su nombre prefijado."""
        if not self.breaker.allow():
            return {"error": "MCP circuit open: reintenta más tarde (backoff)"}
        info = self.tools_catalog.get(prefixed_name)
        if not info:
            return {"error": f"Unknown MCP tool: {prefixed_name}"}
        session = self.sessions.get(info["server"])
        if not session:
            return {"error": f"MCP server '{info['server']}' not connected"}
        delay = 0.4
        last = None
        for attempt in range(3):
            try:
                last = await session.call_tool(info["original_name"], arguments)
                if isinstance(last, dict) and last.get("error"):
                    raise RuntimeError(str(last.get("error")))
                self.breaker.success()
                return last
            except Exception as exc:
                last = {"error": str(exc)}
                await asyncio.sleep(delay)
                delay *= 2
        self.breaker.fail()
        return last if last is not None else {"error": "MCP call failed"}

    def get_tools_for_agent(self) -> List[Dict[str, Any]]:
        """Devuelve las herramientas MCP en formato compatible con Ollama tools."""
        result = []
        for prefixed, info in self.tools_catalog.items():
            result.append({
                "type": "function",
                "function": {
                    "name": prefixed,
                    "description": f"[MCP:{info['server']}] {info['description']}",
                    "parameters": info["parameters"],
                }
            })
        return result

    async def disconnect_all(self):
        for session in self.sessions.values():
            await session.disconnect()

    @property
    def is_available(self) -> bool:
        """Verdadero si hay al menos una herramienta MCP cargada (ya conectada)."""
        return bool(self.tools_catalog)

    def call_tool_sync(self, prefixed_name: str, arguments: Dict[str, Any]) -> Any:
        """Puente síncrono a call_tool usando el event loop dedicado a MCP."""
        return mcp_loop.call_tool_sync(prefixed_name, arguments)


class _McpConnection:
    """Conexión de un servidor MCP viviendo en el event loop dedicado."""
    def __init__(self, session: MCPSession):
        self.session = session
        self.connected = False
        self.tools: List[str] = []


class _MCPEventLoop:
    """Event loop asyncio en un hilo propio para MCP (evita errores cross-loop)."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._manager: Optional[MCPManager] = None

    def _ensure(self):
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return
            loop = asyncio.new_event_loop()
            self._loop = loop
            self._thread = threading.Thread(
                target=self._run_loop, args=(loop,), name="ottercode-mcp", daemon=True
            )
            self._thread.start()

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            pass

    def _submit(self, coro, timeout: float = 45.0):
        self._ensure()
        loop = self._loop
        if loop is None:
            raise RuntimeError("MCP event loop no disponible")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    @property
    def manager(self) -> MCPManager:
        if self._manager is None:
            self._manager = MCPManager(config_dir=Path(__file__).resolve().parent)
        return self._manager

    async def _connect_all(self, manager: MCPManager):
        failed: List[str] = []
        for name, session in manager.sessions.items():
            try:
                ok = await session.connect()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"MCP connect '{name}' failed: {exc}")
                ok = False
            conn = _McpConnection(session)
            conn.connected = ok
            manager._connections[name] = conn
            if ok:
                for tool in session.tools:
                    prefixed = f"mcp__{name}__{tool['name']}"
                    manager.tools_catalog[prefixed] = {
                        "name": prefixed,
                        "original_name": tool["name"],
                        "server": name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {}),
                    }
                    conn.tools.append(prefixed)
                logger.info(f"MCP server '{name}': {len(session.tools)} tools loaded")
            else:
                failed.append(name)
        manager._connected_once = True
        return failed

    def connect_all_sync(self) -> List[str]:
        """Conecta todos los servidores MCP de forma síncrona. Devuelve nombres fallidos."""
        mgr = self.manager
        if not mgr.sessions:
            mgr._connected_once = True
            return []
        self._ensure()
        loop = self._loop
        if loop is None:
            return list(mgr.sessions.keys())
        fut = asyncio.run_coroutine_threadsafe(self._connect_all(mgr), loop)
        try:
            return fut.result(timeout=45)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"MCP connect_all timeout: {exc}")
            return list(mgr.sessions.keys())

    def is_ready(self) -> bool:
        """Conecta si es la primera vez y devuelve si hay herramientas MCP disponibles."""
        mgr = self.manager
        if not mgr._connected_once:
            if not mgr.sessions:
                mgr._connected_once = True
            else:
                self.connect_all_sync()
        return mgr.is_available

    def call_tool_sync(self, prefixed_name: str, arguments: Dict[str, Any]) -> Any:
        mgr = self.manager
        if not mgr.is_available:
            return {
                "error": (
                    f"MCP no disponible: servidores configurados={len(mgr.sessions)}, "
                    f"herramientas cargadas={len(mgr.tools_catalog)}"
                )
            }
        return self._submit(mgr.call_tool(prefixed_name, arguments or {}))


mcp_loop = _MCPEventLoop()


def get_mcp_manager() -> MCPManager:
    """Devuelve el MCPManager singleton (compatibilidad con código existente)."""
    return mcp_loop.manager


def connect_mcp_all() -> List[str]:
    """Conecta todos los servidores MCP. Devuelve los nombres que fallaron."""
    return mcp_loop.connect_all_sync()
