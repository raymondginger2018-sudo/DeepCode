#!/usr/bin/env python3
"""
Sub-Agent System v2.0 — Claude Code 风格多代理协作引擎（增强版）
=================================================================
参考: Claude Code v2.1.88 SdkControlTransport.ts / tasks/

改进点（v1.0 → v2.0）:
  - Transport 桥接模式: 主进程↔子代理通过结构化消息通信
  - 请求/响应关联追踪: message_id 贯穿全链路
  - SdkControlClientTransport / SdkControlServerTransport: 模拟 Claude Code 的双向 transport
  - 超时控制: 子代理任务超时自动取消
  - 任务结果持久化: 完成的任务结果写入磁盘

用法:
  from sub_agent import SubAgentSystem
  sa = SubAgentSystem()
  result = await sa.spawn_sub_agent("analyze_stock", {"code": "600316"})
"""

import asyncio
import uuid
import time
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class TaskType(Enum):
    ANALYSIS = "analysis"
    RESEARCH = "research"
    DREAM = "dream"
    TEAMMATE = "teammate"
    SCRAPE = "scrape"
    MONITOR = "monitor"


class TaskPriority(Enum):
    HIGH = 1
    MEDIUM = 2
    LOW = 3
    BACKGROUND = 4


# ══════════════════════════════════════════════
# Transport 桥接 — 主进程↔子代理通信
# ══════════════════════════════════════════════

class TransportMessage:
    """
    结构化传输消息 (参考 Claude Code SdkControlTransport.ts)
    用于主进程和子代理之间的通信
    """
    def __init__(self, msg_type: str, payload: Any = None,
                 message_id: Optional[str] = None,
                 correlation_id: Optional[str] = None):
        self.message_id = message_id or str(uuid.uuid4())
        self.correlation_id = correlation_id  # 关联 ID，用于匹配请求/响应
        self.msg_type = msg_type  # "request" / "response" / "error" / "stream"
        self.payload = payload
        self.timestamp = time.time()

    def to_dict(self) -> Dict:
        return {
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "type": self.msg_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TransportMessage":
        msg = cls(
            msg_type=data.get("type", ""),
            payload=data.get("payload"),
            message_id=data.get("message_id"),
            correlation_id=data.get("correlation_id"),
        )
        msg.timestamp = data.get("timestamp", time.time())
        return msg


class SdkControlClientTransport:
    """
    客户端 Transport — 主进程端 (参考 Claude Code SdkControlClientTransport)

    子代理的 MCP 风格通信:
      - send(message): 发送请求，等待响应
      - 通过 message_id 关联请求和响应
      - 支持超时
    """
    def __init__(self, handler: Callable[[TransportMessage], Any],
                 timeout_ms: float = 30000):
        self.handler = handler
        self.timeout_ms = timeout_ms
        self._pending: Dict[str, asyncio.Future] = {}

    async def send(self, message: TransportMessage) -> TransportMessage:
        """
        发送消息并等待响应
        返回关联的响应消息
        """
        future: asyncio.Future = asyncio.Future()
        self._pending[message.message_id] = future

        try:
            # 通过 handler 处理（实际会转发到子代理）
            result = self.handler(message)

            if asyncio.iscoroutine(result):
                response_payload = await result
            else:
                response_payload = result

            # 如果 handler 直接返回了结果，构造响应
            if isinstance(response_payload, TransportMessage):
                return response_payload

            # 否则等待 Future（异步响应模式）
            try:
                response = await asyncio.wait_for(
                    future,
                    timeout=self.timeout_ms / 1000,
                )
                return response
            except asyncio.TimeoutError:
                raise TimeoutError(f"Sub-agent timeout ({self.timeout_ms}ms)")
        finally:
            self._pending.pop(message.message_id, None)

    def resolve(self, message_id: str, response: TransportMessage):
        """解析挂起的请求"""
        future = self._pending.get(message_id)
        if future and not future.done():
            future.set_result(response)

    def reject(self, message_id: str, error: Exception):
        """拒绝挂起的请求"""
        future = self._pending.get(message_id)
        if future and not future.done():
            future.set_exception(error)


class SdkControlServerTransport:
    """
    服务端 Transport — 子代理端 (参考 Claude Code SdkControlServerTransport)

    接收主进程的请求，处理并返回响应
    """
    def __init__(self, handler: Callable[[TransportMessage], Any]):
        self.handler = handler
        self.onmessage: Optional[Callable] = None

    async def send(self, message: TransportMessage):
        """发送响应回主进程"""
        if self.onmessage:
            result = self.onmessage(message)
            if asyncio.iscoroutine(result):
                await result

    def receive(self, message: TransportMessage) -> Any:
        """接收并处理请求"""
        if self.handler:
            result = self.handler(message)
            return result
        return None


# ══════════════════════════════════════════════
# 子代理任务
# ══════════════════════════════════════════════

@dataclass
class SubAgentTask:
    """子代理任务 v2.0 (增加 Transport 通信支持)"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: TaskType = TaskType.ANALYSIS
    name: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.MEDIUM
    status: str = "pending"  # pending/running/done/error/timeout
    result: Any = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    parent_task_id: Optional[str] = None

    # v2.0: Transport 通信
    transport: Optional[SdkControlServerTransport] = None
    pending_requests: Dict[str, asyncio.Future] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        if self.started_at and self.completed_at:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at)
            return (end - start).total_seconds()
        return 0


# ══════════════════════════════════════════════
# 子代理系统
# ══════════════════════════════════════════════

class SubAgentSystem:
    """
    多代理协作系统 v2.0
    模拟 Claude Code 的 Task 系统 + SdkControlTransport
    """

    def __init__(self, max_workers: int = 4,
                 result_dir: Optional[str] = None):
        self.max_workers = max_workers
        self.tasks: Dict[str, SubAgentTask] = {}
        self.handlers: Dict[str, Callable] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_workers)

        # v2.0: Transport 路由
        self._client_transport = SdkControlClientTransport(
            handler=self._on_transport_message
        )

        # v2.0: 结果持久化
        self.result_dir = result_dir
        if result_dir:
            os.makedirs(result_dir, exist_ok=True)

        self.stats = {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "timeout_tasks": 0,
            "total_duration_ms": 0,
            "transport_messages": 0,
        }

    def register_handler(self, task_type: str, handler: Callable):
        """注册任务类型的处理器"""
        self.handlers[task_type] = handler

    def get_client_transport(self) -> SdkControlClientTransport:
        """获取客户端 Transport (供主进程使用)"""
        return self._client_transport

    async def _on_transport_message(self, message: TransportMessage) -> Any:
        """
        Transport 消息处理路由
        如果是子代理上报的结果，路由到对应的 pending 请求
        """
        self.stats["transport_messages"] += 1

        if message.msg_type == "response" and message.correlation_id:
            # 查找并解析挂起的请求
            for task in self.tasks.values():
                if message.correlation_id in task.pending_requests:
                    future = task.pending_requests.pop(message.correlation_id, None)
                    if future and not future.done():
                        future.set_result(message)
                        break
        return message

    async def spawn_sub_agent(
        self,
        task_name: str,
        params: Dict[str, Any],
        priority: TaskPriority = TaskPriority.MEDIUM,
        parent_task_id: str = None,
        timeout_ms: float = 0,
    ) -> SubAgentTask:
        """
        创建子代理处理独立子任务 v2.0

        参考 Claude Code: LocalAgentTask + SdkControlClientTransport

        参数:
          task_name: 任务名称
          params: 任务参数
          priority: 优先级
          parent_task_id: 父任务 ID
          timeout_ms: 超时时间 (0 = 不超时)
        """
        task = SubAgentTask(
            type=TaskType.ANALYSIS,
            name=task_name,
            params=params,
            priority=priority,
            parent_task_id=parent_task_id,
        )

        # v2.0: 创建 Server Transport
        server_transport = SdkControlServerTransport(
            handler=lambda msg: self._handle_task_message(task, msg)
        )
        task.transport = server_transport

        self.tasks[task.id] = task
        self.stats["total_tasks"] += 1

        if timeout_ms > 0:
            # 带超时的 spawn
            try:
                await asyncio.wait_for(
                    self._execute_with_queue(task),
                    timeout=timeout_ms / 1000,
                )
            except asyncio.TimeoutError:
                task.status = "timeout"
                task.error = f"子代理任务超时 ({timeout_ms}ms)"
                self.stats["timeout_tasks"] += 1
        else:
            await self._execute_with_queue(task)

        return task

    async def _execute_with_queue(self, task: SubAgentTask):
        """将任务加入队列等待执行"""
        await self._queue.put(task)

    async def teammate_collaborate(
        self,
        tasks_config: List[Dict[str, Any]],
    ) -> List[SubAgentTask]:
        """多代理协作 — 并行执行多个分析任务"""
        teammate_tasks = []
        for cfg in tasks_config:
            task = SubAgentTask(
                type=TaskType.TEAMMATE,
                name=cfg["name"],
                params=cfg.get("params", {}),
                priority=TaskPriority.HIGH,
            )
            self.tasks[task.id] = task
            self.stats["total_tasks"] += 1
            await self._queue.put(task)
            teammate_tasks.append(task)

        return teammate_tasks

    async def dream_task(
        self,
        task_name: str,
        params: Dict[str, Any],
    ) -> SubAgentTask:
        """后台异步任务 — 不阻塞主流程"""
        task = SubAgentTask(
            type=TaskType.DREAM,
            name=task_name,
            params=params,
            priority=TaskPriority.BACKGROUND,
        )
        self.tasks[task.id] = task
        self.stats["total_tasks"] += 1
        await self._queue.put(task)
        return task

    def _handle_task_message(self, task: SubAgentTask, message: TransportMessage) -> Any:
        """
        处理子代理的消息

        子代理可以通过 Transport 向主进程发送中间结果或请求更多信息
        """
        if message.msg_type == "request":
            # 子代理请求更多信息 → 路由到主进程
            future = asyncio.get_event_loop().create_future()
            task.pending_requests[message.message_id] = future
            return future

        elif message.msg_type == "stream":
            # 子代理的流式中间结果
            return {"ack": True, "received_seq": message.payload.get("seq", 0)}

        return None

    async def start_worker(self):
        """启动工作协程 v2.0"""
        while True:
            task = await self._queue.get()
            if task is None:
                break

            async with self._semaphore:
                try:
                    await self._execute_task(task)
                except Exception as e:
                    task.status = "error"
                    task.error = str(e)
                    self.stats["failed_tasks"] += 1

            self._queue.task_done()

    async def _execute_task(self, task: SubAgentTask):
        """执行单个任务 v2.0 (增加 Transport 通信和结果持久化)"""
        task.status = "running"
        task.started_at = datetime.now().isoformat()
        start = time.time()

        try:
            handler = self.handlers.get(task.name)
            if handler:
                if asyncio.iscoroutinefunction(handler):
                    task.result = await handler(**task.params)
                else:
                    task.result = handler(**task.params)
            else:
                task.result = self._default_handler(task)
        except Exception as e:
            task.status = "error"
            task.error = str(e)
            self.stats["failed_tasks"] += 1
            return

        task.status = "done"
        task.completed_at = datetime.now().isoformat()
        self.stats["completed_tasks"] += 1
        duration = round((time.time() - start) * 1000)
        self.stats["total_duration_ms"] += duration

        # v2.0: 结果持久化
        self._persist_result(task)

    def _persist_result(self, task: SubAgentTask):
        """持久化任务结果到磁盘"""
        if not self.result_dir:
            return
        try:
            result_file = os.path.join(
                self.result_dir,
                f"task_{task.id}_{task.name}_{datetime.now().strftime('%Y%m%d')}.json"
            )
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump({
                    "id": task.id,
                    "name": task.name,
                    "type": task.type.value,
                    "status": task.status,
                    "duration": task.duration,
                    "params": task.params,
                    "result": str(task.result)[:5000] if task.result else None,
                    "error": task.error,
                    "created_at": task.created_at,
                    "completed_at": task.completed_at,
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _default_handler(self, task: SubAgentTask) -> Dict:
        """默认处理"""
        return {
            "task_id": task.id,
            "task_name": task.name,
            "params": task.params,
            "note": "No handler registered — task queued for processing",
        }

    def get_task(self, task_id: str) -> Optional[SubAgentTask]:
        return self.tasks.get(task_id)

    def get_task_status(self, task_id: str) -> Optional[Dict]:
        task = self.tasks.get(task_id)
        if not task:
            return None
        return {
            "id": task.id,
            "name": task.name,
            "status": task.status,
            "type": task.type.value,
            "priority": task.priority.name,
            "duration": task.duration,
            "created_at": task.created_at,
            "error": task.error,
        }

    def list_tasks(self, status: str = None) -> List[Dict]:
        """列出所有任务"""
        tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return [
            {
                "id": t.id,
                "name": t.name,
                "status": t.status,
                "type": t.type.value,
                "duration": t.duration,
            }
            for t in tasks
        ]

# ── 全局单例工厂 ──

_sub_agent_system: Optional[SubAgentSystem] = None


def get_sub_agent_system(max_workers: int = 4, result_dir: str = None) -> SubAgentSystem:
    """获取/创建全局 SubAgentSystem 实例"""
    global _sub_agent_system
    if _sub_agent_system is None:
        _sub_agent_system = SubAgentSystem(max_workers=max_workers, result_dir=result_dir)
    return _sub_agent_system


    async def cancel_task(self, task_id: str) -> bool:
        """取消正在运行的任务"""
        task = self.tasks.get(task_id)
        if not task or task.status != "running":
            return False
        task.status = "error"
        task.error = "任务被取消"
        return True
