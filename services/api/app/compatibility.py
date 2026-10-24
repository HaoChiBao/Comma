MISSING_COMPONENT = object()
MISSING_KEY = object()


def _lookup(specs: dict, path: str):
    component, key = path.split(".", 1)
    if component not in specs:
        return MISSING_COMPONENT
    if key not in specs[component]:
        return MISSING_KEY
    return specs[component][key]


def problem(specs: dict, rules: list[dict]) -> str | None:
    """Return a compatibility failure, or None when the chosen specs can be built.

    Rules that mention a component not yet chosen are skipped so partial allocations
    can be searched. A chosen component with a missing required attribute fails closed.
    """
    for rule in rules:
        issue = _eval(specs, rule)
        if issue:
            return issue
    return None


def _eval(specs: dict, rule: dict) -> str | None:
    kind = rule["rule_type"]
    constraint = rule["constraint"]
    message = constraint.get("message") or kind
    if kind == "equals":
        left = _lookup(specs, constraint["left"])
        right = _lookup(specs, constraint["right"])
        if left is MISSING_COMPONENT or right is MISSING_COMPONENT:
            return None
        if left is MISSING_KEY or right is MISSING_KEY or left != right:
            return message
        return None
    if kind == "member":
        value = _lookup(specs, constraint["value"])
        choices = _lookup(specs, constraint["choices"])
        if value is MISSING_COMPONENT or choices is MISSING_COMPONENT:
            return None
        if value is MISSING_KEY or choices is MISSING_KEY or value not in set(choices):
            return message
        return None
    if kind == "wattage":
        parts = [_lookup(specs, path) for path in constraint["sum"]]
        minimum = _lookup(specs, constraint["minimum"])
        if any(part is MISSING_COMPONENT for part in parts) or minimum is MISSING_COMPONENT:
            return None
        if any(part is MISSING_KEY for part in parts) or minimum is MISSING_KEY:
            return message
        need = sum(int(part) for part in parts) + int(constraint["headroom"])
        if int(minimum) < need:
            return message
        return None
    return f"unknown rule {kind}"
