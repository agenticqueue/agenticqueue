def test_actors_support_crud_filtering_and_audit(
    exercise_core_entity_crud_flow,
    core_specs_by_resource,
    client,
    session_factory,
    deps,
) -> None:
    exercise_core_entity_crud_flow(
        core_specs_by_resource["actors"], client, session_factory, deps
    )


def test_actors_reject_missing_expired_and_scope_mismatch_tokens(
    assert_core_entity_auth_failures,
    core_specs_by_resource,
    client,
    session_factory,
    deps,
) -> None:
    assert_core_entity_auth_failures(
        core_specs_by_resource["actors"], client, session_factory, deps
    )
