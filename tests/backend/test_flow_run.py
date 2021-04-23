"""
Tests for `FlowRunView`
"""
import pytest

from prefect.backend import FlowRunView, TaskRunView
from unittest.mock import MagicMock
from prefect.utilities.graphql import EnumValue
from prefect.engine.state import Success, Running
from prefect.engine.results import LocalResult
from prefect.storage import Local

FLOW_RUN_DATA_1 = {
    "id": "id-1",
    "name": "name-1",
    "flow_id": "flow_id-1",
    "serialized_state": Success(message="state-1").serialize(),
}
FLOW_RUN_DATA_2 = {
    "id": "id-2",
    "name": "name-2",
    "flow_id": "flow_id-2",
    "serialized_state": Success(message="state-2").serialize(),
}

TASK_RUN_DATA_FINISHED = {
    "id": "task-run-id-1",
    "name": "name-1",
    "task": {"id": "task-id-1", "slug": "task-slug-1"},
    "map_index": "map_index-1",
    "serialized_state": Success(message="state-1").serialize(),
    "flow_run_id": "flow_run_id-1",
}
TASK_RUN_DATA_RUNNING = {
    "id": "task-run-id-2",
    "name": "name-2",
    "task": {"id": "task-id-2", "slug": "task-slug-2"},
    "map_index": "map_index-2",
    "serialized_state": Running(message="state-2").serialize(),
    "flow_run_id": "flow_run_id-2",
}


def test_flow_run_view_query_for_flow_run_raises_bad_responses(patch_post):
    patch_post({})

    with pytest.raises(ValueError, match="bad result while querying for flow runs"):
        FlowRunView.query_for_flow_run(where={})


def test_flow_run_view_query_for_flow_run_raises_when_not_found(patch_post):
    patch_post({"data": {"flow_run": []}})

    with pytest.raises(ValueError, match="No flow runs found"):
        FlowRunView.query_for_flow_run(where={})


def test_flow_run_view_query_for_flow_run_errors_on_multiple_flow_runs(patch_post):
    patch_post({"data": {"flow_run": [1, 2]}})

    with pytest.raises(ValueError, match=r"multiple \(2\) flow runs"):
        FlowRunView.query_for_flow_run(where={})


def test_flow_run_view_query_for_flow_run_unpacks_result_singleton(patch_post):
    patch_post({"data": {"flow_run": [1]}})

    assert FlowRunView.query_for_flow_run(where={}) == 1


def test_flow_run_view_query_for_flow_run_uses_where_in_query(monkeypatch):
    post = MagicMock(return_value={"data": {"flow_run": [FLOW_RUN_DATA_1]}})
    monkeypatch.setattr("prefect.client.client.Client.post", post)

    FlowRunView.query_for_flow_run(where={"foo": {"_eq": "bar"}})

    assert (
        'flow_run(where: { foo: { _eq: "bar" } })'
        in post.call_args[1]["params"]["query"]
    )


def test_flow_run_view_query_for_flow_run_includes_all_required_data(monkeypatch):
    graphql = MagicMock(return_value={"data": {"flow_run": [FLOW_RUN_DATA_1]}})
    monkeypatch.setattr("prefect.client.client.Client.graphql", graphql)

    FlowRunView.query_for_flow_run(where={})

    query_dict = graphql.call_args[0][0]
    selection_set = query_dict["query"]["flow_run(where: {})"]
    assert selection_set == {
        "id": True,
        "name": True,
        "serialized_state": True,
        "flow_id": True,
    }


def test_flow_run_view_from_returns_instance(
    patch_post,
):
    patch_post({"data": {"flow_run": [FLOW_RUN_DATA_1]}})

    flow_run = FlowRunView.from_flow_run_id("id-1", load_static_tasks=False)

    assert flow_run.flow_run_id == "id-1"
    assert flow_run.name == "name-1"
    assert flow_run.flow_id == "flow_id-1"
    # This state is deserialized at initialization
    assert flow_run.state == Success(message="state-1")
    # There are no cached tasks
    assert flow_run.cached_task_runs == {}


def test_flow_run_view_from_returns_instance_with_loaded_static_tasks(
    patch_posts,
):
    patch_posts(
        [
            {"data": {"flow_run": [FLOW_RUN_DATA_1]}},
            {"data": {"task_run": [TASK_RUN_DATA_FINISHED, TASK_RUN_DATA_RUNNING]}},
        ]
    )

    flow_run = FlowRunView.from_flow_run_id("id-1", load_static_tasks=True)

    assert flow_run.flow_run_id == "id-1"
    assert flow_run.name == "name-1"
    assert flow_run.flow_id == "flow_id-1"
    # This state is deserialized at initialization
    assert flow_run.state == Success(message="state-1")

    # Only the finished task is cached
    assert len(flow_run.cached_task_runs) == 1
    assert flow_run.cached_task_runs["task-run-id-1"] == TaskRunView.from_task_run_data(
        TASK_RUN_DATA_FINISHED
    )


def test_flow_run_view_get_latest_returns_new_instance(patch_post, patch_posts):
    patch_posts(
        [
            {"data": {"flow_run": [FLOW_RUN_DATA_1]}},
            {"data": {"task_run": [TASK_RUN_DATA_FINISHED, TASK_RUN_DATA_RUNNING]}},
        ]
    )

    flow_run = FlowRunView.from_flow_run_id("fake-id", load_static_tasks=True)

    patch_post({"data": {"flow_run": [FLOW_RUN_DATA_2]}})

    flow_run_2 = flow_run.get_latest()

    # Assert we have not mutated the original flow run object
    assert flow_run.flow_run_id == "id-1"
    assert flow_run.name == "name-1"
    assert flow_run.flow_id == "flow_id-1"
    assert flow_run.state == Success(message="state-1")
    assert len(flow_run.cached_task_runs) == 1
    assert flow_run.cached_task_runs["task-run-id-1"] == TaskRunView.from_task_run_data(
        TASK_RUN_DATA_FINISHED
    )

    # Assert the new object has the data returned by the query
    # In reality, the flow run ids and such would match because that's how the lookup
    # is done
    assert flow_run_2.flow_run_id == "id-2"
    assert flow_run_2.name == "name-2"
    assert flow_run_2.flow_id == "flow_id-2"
    assert flow_run_2.state == Success(message="state-2")

    # Cached task runs are transferred
    assert len(flow_run.cached_task_runs) == 1
    assert flow_run.cached_task_runs["task-run-id-1"] == TaskRunView.from_task_run_data(
        TASK_RUN_DATA_FINISHED
    )


def test_flow_run_view_from_flow_run_id_where_clause(monkeypatch):
    post = MagicMock(return_value={"data": {"flow_run": [FLOW_RUN_DATA_1]}})
    monkeypatch.setattr("prefect.client.client.Client.post", post)

    FlowRunView.from_flow_run_id(flow_run_id="id-1", load_static_tasks=False)

    assert (
        'flow_run(where: { id: { _eq: "id-1" } })'
        in post.call_args[1]["params"]["query"]
    )