import pytest
from durable_agent.crash_testing.scenarios import parse_scenarios


def test_catalog_accepts_custom_presets_and_explicit_no_fault():
    presets = parse_scenarios("""
[my-worker-crash]
hook = "worker.before_report"
description = "Lose a completed response."

[observe-only]
hook = "none"
description = "Observe without killing."
""")
    assert presets["my-worker-crash"] == ("worker.before_report", "Lose a completed response.")
    assert presets["observe-only"] == (None, "Observe without killing.")


@pytest.mark.parametrize(
    "source",
    [
        "",
        "[unfinished",
        '[worker]\ndescription = "Missing hook"',
        '[worker]\nhook = "worker.before_reprot"\ndescription = "Unknown hook"',
        '[worker]\nhook = "worker.after_claim"\ndescription = "No recovery checks for this hook"',
        '[worker]\nhook = "worker.before_report"\ndescription = 42',
        '[worker]\nhook = "worker.before_report"\ndescription = "Crash"\nextra = true',
        '["Invalid name"]\nhook = "none"\ndescription = "Observe"',
        '[baseline]\nhook = "none"\ndescription = ""',
        '[baseline]\nhook = "none"\ndescription = "Observe"\n[baseline]',
    ],
)
def test_catalog_rejects_invalid_definitions_instead_of_arming_them(source):
    with pytest.raises(ValueError):
        parse_scenarios(source)
