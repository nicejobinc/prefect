import asyncio
import threading
from typing import TYPE_CHECKING, Any, Dict, Iterable, Tuple
from uuid import UUID
from contextlib import contextmanager

import httpx

from prefect.orion import schemas
from prefect.orion.api.server import app as orion_app

if TYPE_CHECKING:
    from prefect.flows import Flow


class OrionClient:
    def __init__(self, http_client: httpx.Client = None) -> None:
        # If not given an httpx client, create one that connects to an ephemeral app
        self._client = http_client or _WSGIClient(app=orion_app)

    def post(self, route: str, **kwargs) -> httpx.Response:
        response = self._client.post(route, **kwargs)
        # TODO: We may not _always_ want to raise bad status codes but for now we will
        #       because response.json() will throw misleading errors and this will ease
        #       development
        response.raise_for_status()
        return response

    def get(self, route: str) -> httpx.Response:
        response = self._client.get(route)
        response.raise_for_status()
        return response

    # API methods ----------------------------------------------------------------------

    def hello(self) -> httpx.Response:
        return self.post("/hello")

    def create_flow(self, flow: "Flow") -> UUID:
        flow_data = schemas.actions.FlowCreate(
            name=flow.name,
            tags=flow.tags,
            parameters=flow.parameters,
        )
        response = self.post("/flows/", json=flow_data.json_dict())

        flow_id = response.json().get("id")
        if not flow_id:
            raise Exception(f"Malformed response: {response}")

        # Return the id of the created flow
        return UUID(flow_id)

    def read_flow(self, flow_id: UUID) -> schemas.core.Flow:
        response = self.get(f"/flows/{flow_id}")
        return schemas.core.Flow(**response.json())

    def create_flow_run(
        self,
        flow: "Flow",
        parameters: Dict[str, Any] = None,
        context: dict = None,
        extra_tags: Iterable[str] = None,
        parent_task_run_id: str = None,
    ) -> UUID:
        tags = set(flow.tags).union(extra_tags or [])
        parameters = parameters or {}
        context = context or {}

        # Retrieve the flow id
        flow_id = self.create_flow(flow)

        flow_run_data = schemas.actions.FlowRunCreate(
            flow_id=flow_id,
            flow_version=flow.version,
            parameters=parameters,
            context=context,
            tags=list(tags),
            parent_task_run_id=parent_task_run_id,
        )

        response = self.post("/flow_runs/", json=flow_run_data.json_dict())
        flow_run_id = response.json().get("id")
        if not flow_run_id:
            raise Exception(f"Malformed response: {response}")

        return UUID(flow_run_id)

    def read_flow_run(self, flow_run_id: UUID) -> schemas.core.FlowRun:
        response = self.get(f"/flow_runs/{flow_run_id}")
        return schemas.core.FlowRun(**response.json())


class _WSGIClient:
    """
    Creates a synchronous wrapper for calling an async WSGI application's routes using
    temporary `httpx.AsyncClient` instances and an event loop in a thread.
    """

    def __init__(self, app) -> None:
        self._thread, self._event_loop = self._create_threaded_event_loop()
        self.app = app

    @contextmanager
    def _httpx_client(self):
        """
        Creates a temporary httpx.AsyncClient and clean up on exit

        Since this client is created per request, we are forfeiting the benefits of
        a long-lived HTTP session. However, since this is only intended to be used with
        a WSGI application running in-process, there should not be a meaningful change
        in performance.
        """
        client = httpx.AsyncClient(app=self.app, base_url="http://ephemeral")
        try:
            yield client
        finally:
            self._run_coro(client.aclose())

    # httpx.Client methods -------------------------------------------------------------

    def get(self, route: str, **kwargs: Any) -> httpx.Response:
        with self._httpx_client() as client:
            return self._run_coro(client.get(route, **kwargs))

    def post(self, route: str, **kwargs: Any) -> httpx.Response:
        with self._httpx_client() as client:
            return self._run_coro(client.post(route, **kwargs))

    # Event loop management ------------------------------------------------------------

    def _create_threaded_event_loop(
        self,
    ) -> Tuple[threading.Thread, asyncio.AbstractEventLoop]:
        """
        Spawns an event loop in a daemonic thread.

        Creating a new event loop that runs in a child thread prevents us from throwing
        exceptions when there is already an event loop in the main thread and prevents
        synchronous code in the main thread from blocking the event loop from executing.
        """

        def start_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        loop = asyncio.new_event_loop()

        t = threading.Thread(target=start_loop, args=(loop,), daemon=True)
        t.start()

        return t, loop

    def _run_coro(self, coro):
        if not self._event_loop:
            raise ValueError("Event loop has not been created.")
        if not self._event_loop.is_running():
            raise ValueError("Event loop is not running.")

        future = asyncio.run_coroutine_threadsafe(coro, loop=self._event_loop)
        result = future.result()

        return result

    def __del__(self):
        if self._event_loop.is_running():
            self._event_loop.stop()
