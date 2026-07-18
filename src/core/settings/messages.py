"""The shared invalid-value message helper (stdlib only).

Kept in one place (and short, to fit a panel footnote) so the "unsupported value" wording
never drifts between settings. Domain-specific messages that carry extra detail (e.g. the
scraper retention bounds) live with their own settings and may keep bespoke phrasing.
"""


def unsupported_value_message(key: str, default_display: str | None = None) -> str:
    """Shared 'unsupported <key> value' footnote wording.

    Keeps the invalid-value message identical across settings (only the JSON ``key`` and
    the default it fell back to differ), so the wording never drifts as settings are
    added. When ``default_display`` is given the effective default is named in the
    footnote (``"... (1 month)"``); when it is ``None`` - e.g. a plugin-specific default
    the settings row already shows - the message just flags the rejection.

    Args:
        key (str): The JSON key whose value was rejected.
        default_display (str | None): The effective default, formatted for display, or
            ``None`` to omit the parenthetical.

    Returns:
        str: The footnote wording.
    """
    if default_display is None:
        return f"Unsupported {key} value. Using the default."
    return f"Unsupported {key} value. Using the default ({default_display})."
