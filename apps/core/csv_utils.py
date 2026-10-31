def _is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def csv_safe(value):
    """Neutralise spreadsheet formula injection: text starting with = + - @ (or tab/CR) gets a leading apostrophe.
    Real numbers (e.g. -12.5) are left alone."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(s) else s
