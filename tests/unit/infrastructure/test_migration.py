import copy

import pytest

from core.infrastructure.migration import (
    MigrationError,
    MigrationPhase,
    MigrationPlan,
    MigrationTransition,
    compose_transitions,
    migrate_document,
)


def _phase(name, field):
    def transform(document):
        document = copy.deepcopy(document)
        document[field] = True
        return document

    return MigrationPhase(name, transform)


def test_long_jump_runs_every_transition_once_in_order():
    plan = MigrationPlan(
        4,
        {
            1: MigrationTransition(1, (_phase("one", "v2"),)),
            2: MigrationTransition(2, (_phase("two", "v3"),)),
            3: MigrationTransition(3, (_phase("three", "v4"),)),
        },
        lambda document: None,
    )

    migrated = migrate_document({"schema_version": 1}, plan)

    assert migrated == {
        "schema_version": 4,
        "v2": True,
        "v3": True,
        "v4": True,
    }


def test_framework_phase_precedes_plugin_phase_and_other_plugins_need_no_phase():
    common = (
        MigrationTransition(1, (_phase("framework", "framework"),)),
        MigrationTransition(2),
    )
    private = {2: _phase("plugin", "plugin")}

    composed = compose_transitions(3, common, private)
    plugin = migrate_document(
        {"schema_version": 1},
        MigrationPlan(3, composed, lambda document: None),
    )
    unaffected = migrate_document(
        {"schema_version": 1},
        MigrationPlan(
            3,
            compose_transitions(3, common),
            lambda document: None,
        ),
    )

    assert plugin == {"schema_version": 3, "framework": True, "plugin": True}
    assert unaffected == {"schema_version": 3, "framework": True}


def test_current_document_is_an_idempotent_defensive_copy():
    source = {"schema_version": 1, "items": []}
    migrated = migrate_document(source, MigrationPlan(1, {}, lambda document: None))
    assert migrated == source
    assert migrated is not source


@pytest.mark.parametrize("version", [None, True, 0, -1, 1.5, "1"])
def test_schema_version_is_strict(version):
    with pytest.raises(MigrationError):
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


def test_transition_registry_must_be_contiguous():
    with pytest.raises(ValueError, match="cover every version"):
        MigrationPlan(3, {1: MigrationTransition(1)}, lambda document: None)


def test_phase_cannot_mutate_input_or_own_schema_version():
    def mutating(document):
        document["changed"] = True
        return document

    plan = MigrationPlan(
        2,
        {1: MigrationTransition(1, (MigrationPhase("bad", mutating),))},
        lambda document: None,
    )
    with pytest.raises(MigrationError, match="input was mutated"):
        migrate_document({"schema_version": 1}, plan)

    def versioned(document):
        return {**document, "schema_version": 2}

    plan = MigrationPlan(
        2,
        {1: MigrationTransition(1, (MigrationPhase("bad", versioned),))},
        lambda document: None,
    )
    with pytest.raises(MigrationError, match="must not change"):
        migrate_document({"schema_version": 1}, plan)
