import pytest

from core.exceptions import ConfigFileError
from core.schema_migrations.engine import (
    MigrationError,
    MigrationPhase,
    MigrationPlan,
    document_version,
    migrate_document,
)


def _phase(name, field, calls=None):
    def transform(document):
        if calls is not None:
            calls.append(name)
        return {**document, field: True}

    return MigrationPhase(name, transform)


def test_long_jump_runs_phases_once_in_order_and_engine_owns_versions():
    calls = []

    def observe_version(document):
        calls.append(f"private-v{document['plugin_schema_version']}")
        return {**document, "private": True}

    plan = MigrationPlan(
        "plugin_schema_version",
        4,
        {
            1: (
                _phase("one", "v2", calls),
                MigrationPhase("private", observe_version),
            ),
            2: (),
            3: (_phase("three", "v4", calls),),
        },
    )

    migrated = migrate_document({"plugin_schema_version": 1}, plan)

    assert calls == ["one", "private-v1", "three"]
    assert migrated == {
        "plugin_schema_version": 4,
        "v2": True,
        "private": True,
        "v4": True,
    }


def test_current_document_is_an_idempotent_defensive_copy():
    source = {"schema_version": 1, "items": []}
    migrated = migrate_document(source, MigrationPlan("schema_version", 1, {}))
    assert migrated == source
    assert migrated is not source


@pytest.mark.parametrize("version", [None, True, 0, -1, 1.5, "1"])
def test_document_version_is_strict_for_configurable_key(version):
    with pytest.raises(MigrationError, match="plugin_schema_version must be a positive integer"):
        migrate_document(
            {"plugin_schema_version": version},
            MigrationPlan("plugin_schema_version", 1, {}),
        )


def test_future_schema_fails_closed():
    with pytest.raises(MigrationError, match="newer"):
        migrate_document(
            {"schema_version": 2},
            MigrationPlan("schema_version", 1, {}),
        )


def test_plan_requires_key_strict_coverage_tuple_containers_and_phase_members():
    with pytest.raises(ValueError, match="version key"):
        MigrationPlan("", 1, {})
    with pytest.raises(ValueError, match="cover every version"):
        MigrationPlan("schema_version", 3, {1: ()})
    with pytest.raises(TypeError, match="must be a tuple"):
        MigrationPlan("schema_version", 2, {1: []})
    with pytest.raises(TypeError, match="MigrationPhase"):
        MigrationPlan("schema_version", 2, {1: ("not a phase",)})
    with pytest.raises(ValueError, match="integer source versions"):
        MigrationPlan("schema_version", 2, {True: ()})


def test_plan_defensively_copies_transition_mapping():
    transitions = {1: (_phase("one", "one"),)}
    plan = MigrationPlan("schema_version", 2, transitions)
    transitions.clear()
    assert set(plan.transitions) == {1}


def test_phase_cannot_mutate_input_or_own_version_key():
    def mutating(document):
        document["changed"] = True
        return document

    plan = MigrationPlan(
        "plugin_schema_version",
        2,
        {1: (MigrationPhase("bad", mutating),)},
    )
    with pytest.raises(
        MigrationError,
        match=(
            "plugin_schema_version v1 to v2 phase 'bad' failed: migration engine input was mutated"
        ),
    ):
        migrate_document({"plugin_schema_version": 1}, plan)

    def versioned(document):
        return {**document, "plugin_schema_version": 2}

    plan = MigrationPlan(
        "plugin_schema_version",
        2,
        {1: (MigrationPhase("bad", versioned),)},
    )
    with pytest.raises(MigrationError, match="must not change plugin_schema_version"):
        migrate_document({"plugin_schema_version": 1}, plan)


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
        "schema_version",
        2,
        {1: (MigrationPhase("framework phase", transform),)},
    )
    with pytest.raises(
        MigrationError,
        match=rf"schema_version v1 to v2 phase 'framework phase' failed: .*{message}",
    ):
        migrate_document({"schema_version": 1}, plan)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"unsupported"}, "unsupported JSON type set"),
        (("not", "a", "list"), "unsupported JSON type tuple"),
        ({1: "not a string key"}, "non-string JSON object key"),
        (float("nan"), "finite JSON number"),
    ],
)
def test_phase_results_must_be_strict_json(value, message):
    plan = MigrationPlan(
        "schema_version",
        2,
        {
            1: (
                MigrationPhase(
                    "invalid JSON",
                    lambda document: {**document, "value": value},
                ),
            )
        },
    )

    with pytest.raises(
        MigrationError,
        match=rf"schema_version v1 to v2 phase 'invalid JSON' failed: .*{message}",
    ):
        migrate_document({"schema_version": 1}, plan)


def test_cyclic_phase_result_is_rejected_with_phase_context():
    def cyclic(document):
        result = dict(document)
        result["cycle"] = result
        return result

    plan = MigrationPlan(
        "schema_version",
        2,
        {1: (MigrationPhase("cyclic output", cyclic),)},
    )

    with pytest.raises(
        MigrationError,
        match="phase 'cyclic output' failed: .*cyclic JSON container",
    ):
        migrate_document({"schema_version": 1}, plan)


def test_candidate_copy_failure_is_annotated_with_phase_context():
    class NonCopyableDict(dict):
        def __deepcopy__(self, _memo):
            raise RuntimeError("cannot copy candidate")

    plan = MigrationPlan(
        "schema_version",
        2,
        {
            1: (
                MigrationPhase(
                    "copy candidate",
                    lambda document: NonCopyableDict(document),
                ),
            )
        },
    )

    with pytest.raises(
        MigrationError,
        match=("schema_version v1 to v2 phase 'copy candidate' failed: cannot copy candidate"),
    ):
        migrate_document({"schema_version": 1}, plan)


def test_base_exceptions_propagate_from_phases():
    def interrupt(_document):
        raise KeyboardInterrupt

    plan = MigrationPlan(
        "schema_version",
        2,
        {1: (MigrationPhase("stop", interrupt),)},
    )
    with pytest.raises(KeyboardInterrupt):
        migrate_document({"schema_version": 1}, plan)


def test_source_and_transform_receive_defensive_documents():
    source = {"schema_version": 1, "nested": {"value": 1}}

    def transform(document):
        return {**document, "nested": {"value": 2}}

    migrated = migrate_document(
        source,
        MigrationPlan(
            "schema_version",
            2,
            {1: (MigrationPhase("replace nested", transform),)},
        ),
    )
    assert migrated["nested"] == {"value": 2}
    assert source == {"schema_version": 1, "nested": {"value": 1}}
    assert document_version(migrated, "schema_version") == 2
