from dataclasses import asdict, is_dataclass

from YouTubeBridgeV2.server.security import (
    AuthRequirement,
    PermissionContext,
    PermissionGroup,
    SecretBoundary,
    SecurityErrorResponse,
    resolve_permission_context,
    sanitize_security_error,
)


class FakeClient:
    def __init__(self, host):
        self.host = host


class FakeRequest:
    def __init__(self, *, headers=None, host="203.0.113.10", path="/v2/sessions"):
        self.headers = headers or {}
        self.client = FakeClient(host)
        self.url = type("URL", (), {"path": path})()


def _requirement(**overrides):
    base = {
        "permission_group": PermissionGroup.OPERATOR,
        "valid_api_keys": {
            "operator-secret": PermissionGroup.OPERATOR,
            "display-secret": PermissionGroup.DISPLAY,
            "observer-secret": PermissionGroup.OBSERVER,
            "internal-secret": PermissionGroup.INTERNAL,
        },
        "allow_loopback": False,
    }
    base.update(overrides)
    return AuthRequirement(**base)


def _assert_no_secret(value):
    candidates = [repr(value)]
    if is_dataclass(value) and not isinstance(value, type):
        candidates.append(repr(asdict(value)))
    if isinstance(value, dict):
        candidates.append(repr(value))
    text = "\n".join(candidates).lower()
    for forbidden in (
        "operator-secret",
        "display-secret",
        "observer-secret",
        "internal-secret",
        "authorization",
        "x-youtubebridgev2-api-key",
        "raw_headers",
        "token",
        "bearer",
        "super-private",
    ):
        assert forbidden not in text


def test_missing_api_key_returns_unauthorized():
    result = resolve_permission_context(FakeRequest(), _requirement())

    assert isinstance(result, SecurityErrorResponse)
    assert result.status_code == 401
    assert result.error["code"] == "unauthorized"
    assert result.correlation_id == "security-unavailable"
    _assert_no_secret(result)


def test_invalid_api_key_returns_unauthorized_without_secret():
    result = resolve_permission_context(
        FakeRequest(headers={"x-youtubebridgev2-api-key": "wrong-secret"}),
        _requirement(),
    )

    assert isinstance(result, SecurityErrorResponse)
    assert result.status_code == 401
    assert result.error == {
        "code": "unauthorized",
        "message": "authentication required",
    }
    _assert_no_secret(result)


def test_loopback_access_allows_configured_dev_route():
    result = resolve_permission_context(
        FakeRequest(host="127.0.0.1"),
        _requirement(allow_loopback=True, loopback_group=PermissionGroup.OPERATOR),
    )

    assert isinstance(result, PermissionContext)
    assert result.permission_group == PermissionGroup.OPERATOR
    assert result.auth_method == "loopback"
    assert result.is_loopback is True


def test_loopback_display_route_uses_required_group_by_default():
    result = resolve_permission_context(
        FakeRequest(host="127.0.0.1"),
        _requirement(permission_group=PermissionGroup.DISPLAY, allow_loopback=True),
    )

    assert isinstance(result, PermissionContext)
    assert result.permission_group == PermissionGroup.DISPLAY
    assert "read_display_stream" in result.allowed_actions
    assert "manual_close" not in result.allowed_actions


def test_display_scope_can_read_display_stream():
    result = resolve_permission_context(
        FakeRequest(headers={"x-youtubebridgev2-api-key": "display-secret"}),
        _requirement(permission_group=PermissionGroup.DISPLAY),
    )

    assert isinstance(result, PermissionContext)
    assert result.permission_group == PermissionGroup.DISPLAY
    assert "read_display_stream" in result.allowed_actions
    assert "manual_close" not in result.allowed_actions


def test_display_scope_cannot_call_manual_close():
    result = resolve_permission_context(
        FakeRequest(headers={"x-youtubebridgev2-api-key": "display-secret"}),
        _requirement(permission_group=PermissionGroup.OPERATOR, route_id="manual_close"),
    )

    assert isinstance(result, SecurityErrorResponse)
    assert result.status_code == 403
    assert result.error["code"] == "forbidden"
    _assert_no_secret(result)


def test_operator_scope_can_update_aftertalk_policy():
    result = resolve_permission_context(
        FakeRequest(headers={"x-youtubebridgev2-api-key": "operator-secret"}),
        _requirement(permission_group=PermissionGroup.OPERATOR, route_id="aftertalk_policy"),
    )

    assert isinstance(result, PermissionContext)
    assert result.permission_group == PermissionGroup.OPERATOR
    assert "update_aftertalk_policy" in result.allowed_actions
    assert "manual_close" in result.allowed_actions


def test_route_id_requires_matching_action_even_when_group_matches():
    result = resolve_permission_context(
        FakeRequest(headers={"x-youtubebridgev2-api-key": "display-secret"}),
        _requirement(permission_group=PermissionGroup.DISPLAY, route_id="manual_close"),
    )

    assert isinstance(result, SecurityErrorResponse)
    assert result.status_code == 403
    assert result.error["code"] == "forbidden"
    _assert_no_secret(result)


def test_internal_key_cannot_enter_public_operator_surface():
    result = resolve_permission_context(
        FakeRequest(headers={"x-youtubebridgev2-api-key": "internal-secret"}),
        _requirement(permission_group=PermissionGroup.OPERATOR, route_id="manual_close"),
    )

    assert isinstance(result, SecurityErrorResponse)
    assert result.status_code == 403
    assert result.error["code"] == "forbidden"
    _assert_no_secret(result)


def test_internal_key_can_enter_internal_surface():
    result = resolve_permission_context(
        FakeRequest(headers={"x-youtubebridgev2-api-key": "internal-secret"}),
        _requirement(permission_group=PermissionGroup.INTERNAL),
    )

    assert isinstance(result, PermissionContext)
    assert result.permission_group == PermissionGroup.INTERNAL
    assert "internal_service_call" in result.allowed_actions


def test_security_error_does_not_include_raw_headers():
    error = sanitize_security_error(
        {
            "code": "unauthorized",
            "message": "bad token operator-secret",
            "raw_headers": {
                "authorization": "Bearer super-private",
                "x-youtubebridgev2-api-key": "operator-secret",
            },
        },
        correlation_id="corr-1",
        status_code=401,
    )

    assert isinstance(error, SecurityErrorResponse)
    assert error.status_code == 401
    assert error.correlation_id == "corr-1"
    assert error.error == {
        "code": "unauthorized",
        "message": "authentication required",
    }
    _assert_no_secret(error)


def test_security_error_code_is_allowlisted():
    error = sanitize_security_error(
        {
            "code": "operator-secret raw token",
            "message": "bad token operator-secret",
        },
        correlation_id="corr-2",
        status_code=401,
    )

    assert error.error == {
        "code": "unauthorized",
        "message": "authentication required",
    }
    _assert_no_secret(error)


def test_memoria_secret_is_exposed_only_as_boundary_reference():
    boundary = SecretBoundary(
        reference_id="memoria-auth-ref",
        secret_kind="memoria_auth",
        secret_value="super-private",
        public_metadata={"issuer": "v2", "token": "must not leak"},
    )

    assert boundary.as_adapter_reference() == {
        "reference_id": "memoria-auth-ref",
        "secret_kind": "memoria_auth",
        "public_metadata": {"issuer": "v2"},
    }
    assert "secret_value" not in asdict(boundary)
    _assert_no_secret(boundary)
    _assert_no_secret(boundary.as_adapter_reference())


def test_secret_boundary_asdict_redacts_public_metadata():
    boundary = SecretBoundary(
        reference_id="memoria-auth-ref",
        secret_kind="memoria_auth",
        public_metadata={
            "issuer": "v2",
            "raw_headers": {"authorization": "Bearer super-private"},
            "nested": {"token": "operator-secret", "safe": "ok"},
        },
    )

    assert asdict(boundary) == {
        "reference_id": "memoria-auth-ref",
        "secret_kind": "memoria_auth",
        "public_metadata": {"issuer": "v2", "nested": {"safe": "ok"}},
    }
    _assert_no_secret(boundary)


def test_auth_requirement_serialization_does_not_expose_raw_api_keys():
    requirement = _requirement()

    _assert_no_secret(requirement)
    serialized = asdict(requirement)
    assert "valid_api_keys" not in serialized
    _assert_no_secret(serialized)
