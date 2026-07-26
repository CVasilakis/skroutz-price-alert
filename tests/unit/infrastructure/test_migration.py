import pytest

from core.exceptions import ConfigFileError
from core.infrastructure.migration import (
    MigrationError,
    MigrationPhase,
    MigrationPlan,
    migrate_document,
)
from core.scrapers.framework import migrations as framework_migrations


def _phase(name, field, calls=None):
    def transform(document):
        if calls is not None:
            calls.append(name)
        return {**document, field: True}

    return MigrationPhase(name, transform)


def test_long_jump_runs_phases_once_in_order_and_engine_owns_versions():
    calls = []

    def observe_version(document):
        calls.append(f"private-v{document['schema_version']}")
        return {**document, "private": True}

    plan = MigrationPlan(
        4,
        {
            1: (_phase("one", "v2", calls), MigrationPhase("private", observe_version)),
            2: (),
            3: (_phase("three", "v4", calls),),
        },
        lambda document: None,
    )

    migrated = migrate_document({"schema_version": 1}, plan)

    assert calls == ["one", "private-v1", "three"]
    assert migrated == {
        "schema_version": 4,
        "v2": True,
        "private": True,
        "v4": True,
    }


def test_framework_phase_precedes_optional_plugin_phase():
    framework = {
        1: (_phase("framework", "framework"),),
        2: (),
    }
    private = {2: _phase("plugin", "plugin")}
    old = framework_migrations.TARGET_CONFIG_TRANSITIONS
    framework_migrations.TARGET_CONFIG_TRANSITIONS = framework
    try:
        composed = framework_migrations._combine_target_config_phases(private)
        unaffected = framework_migrations._combine_target_config_phases()
    finally:
        framework_migrations.TARGET_CONFIG_TRANSITIONS = old

    plugin = migrate_document(
        {"schema_version": 1},
        MigrationPlan(3, composed, lambda document: None),
    )
    plain = migrate_document(
        {"schema_version": 1},
        MigrationPlan(3, unaffected, lambda document: None),
    )

    assert plugin == {"schema_version": 3, "framework": True, "plugin": True}
    assert plain == {"schema_version": 3, "framework": True}


def test_current_document_is_an_idempotent_defensive_copy():
    source = {"schema_version": 1, "items": []}
    migrated = migrate_document(source, MigrationPlan(1, {}, lambda document: None))
    assert migrated == source
    assert migrated is not source


@pytest.mark.parametrize("version", [None, True, 0, -1, 1.5, "1"])
def test_schema_version_is_strict(version):
    with pytest.raises(MigrationError, match="positive integer"):
        migrate_document(
            {"schema_version": version},
            MigrationPlan(1, {}, lambda document: None),
        )


def test_future_schema_fails_closed():
    with pytest.raises(MigrationError, match="newer"):
        migrate_document(
            {"schema_version": 2},
            MigrationPlan(1, {}, lambda document: None),
        )


def test_plan_requires_strict_coverage_tuple_containers_and_phase_members():
    with pytest.raises(ValueError, match="cover every version"):
        MigrationPlan(3, {1: ()}, lambda document: None)
    with pytest.raises(TypeError, match="must be a tuple"):
        MigrationPlan(2, {1: []}, lambda document: None)
    with pytest.raises(TypeError, match="MigrationPhase"):
        MigrationPlan(2, {1: ("not a phase",)}, lambda document: None)
    with pytest.raises(TypeError, match="validator"):
        MigrationPlan(1, {}, None)
    with pytest.raises(ValueError, match="integer source versions"):
        MigrationPlan(2, {True: ()}, lambda document: None)


def test_plan_defensively_copies_transition_mapping():
    transitions = {1: (_phase("one", "one"),)}
    plan = MigrationPlan(2, transitions, lambda document: None)
    transitions.clear()
    assert set(plan.transitions) == {1}


def test_phase_cannot_mutate_input_or_own_schema_version():
    def mutating(document):
        document["changed"] = True
        return document

    plan = MigrationPlan(
        2,
        {1: (MigrationPhase("bad", mutating),)},
        lambda document: None,
    )
    with pytest.raises(
        MigrationError,
        match=r"v1 to v2 phase 'bad' failed: migration engine input was mutated",
    ):
        migrate_document({"schema_version": 1}, plan)

    def versioned(document):
        return {**document, "schema_version": 2}

    plan = MigrationPlan(
        2,
        {1: (MigrationPhase("bad", versioned),)},
        lambda document: None,
    )
    with pytest.raises(
        MigrationError,
        match=r"v1 to v2 phase 'bad' failed: migration phases must not change",
    ):
        migrate_document({"schema_version": 1}, plan)


@pytest.mark.parametrize(
    "transform,message",
    [
        (lambda document: [], "must return a JSON object"),
        (lambda document: (_ for _ in ()).throw(RuntimeError("boom")), "boom"),
        (lambda document: (_ for _ in ()).throw(ConfigFileError("bad config")), "bad config"),
    ],
)
def test_every_ordinary_phase_failure_is_annotated(transform, message):
    plan = MigrationPlan(
        2,
        {1: (MigrationPhase("private phase", transform),)},
        lambda document: None,
    )
    with pytest.raises(
        MigrationError,
        match=rf"v1 to v2 phase 'private phase' failed: .*{message}",
    ):
        migrate_document({"schema_version": 1}, plan)


def test_current_validator_project_exception_is_wrapped_and_annotated():
    def invalid(_document):
        raise ConfigFileError("invalid target")

    with pytest.raises(
        MigrationError,
        match="current-schema validation at v1 failed: invalid target",
    ):
        migrate_document({"schema_version": 1}, MigrationPlan(1, {}, invalid))


def test_base_exceptions_propagate_from_phases_and_validators():
    def interrupt(_document):
        raise KeyboardInterrupt

    phase_plan = MigrationPlan(2, {1: (MigrationPhase("stop", interrupt),)}, lambda _: None)
    with pytest.raises(KeyboardInterrupt):
        migrate_document({"schema_version": 1}, phase_plan)

    validator_plan = MigrationPlan(1, {}, interrupt)
    with pytest.raises(KeyboardInterrupt):
        migrate_document({"schema_version": 1}, validator_plan)


def test_source_and_validator_receive_defensive_documents():
    source = {"schema_version": 1, "nested": {"value": 1}}

    def validate(document):
        document["nested"]["value"] = 2

    migrated = migrate_document(source, MigrationPlan(1, {}, validate))
    assert migrated == source
    assert source == {"schema_version": 1, "nested": {"value": 1}}
