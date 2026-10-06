"""Shared dispatch-limit ownership contracts."""


def test_dispatch_limit_is_owned_below_the_http_layer() -> None:
    """Every dispatch surface shares the business-layer call ceiling."""
    from general_ludd.dispatch.limits import MAX_CALLS_PER_REQUEST
    from general_ludd.execution.tool_loop import MAX_TOOL_CALLS_PER_RESPONSE
    from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST as ROUTER_LIMIT

    assert MAX_CALLS_PER_REQUEST == 20
    assert ROUTER_LIMIT == MAX_CALLS_PER_REQUEST
    assert MAX_TOOL_CALLS_PER_RESPONSE == MAX_CALLS_PER_REQUEST
