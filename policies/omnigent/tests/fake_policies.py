class Verdict:
    def __init__(self, value):
        self.value = value


def asker(*, reason="needs approval"):
    return lambda event: {"result": "ASK", "reason": reason}


def enum_asker():
    return lambda event: {"result": Verdict("ask"), "reason": "enum ask"}


def allower():
    return lambda event: {"result": "ALLOW", "reason": ""}


def denier():
    return lambda event: {"result": "DENY", "reason": "no"}


def abstainer():
    return lambda event: None


def two_arg(*, marker):
    def evaluate(event, config):
        return {"result": "ASK", "reason": f"{marker}:{sorted(config)}"}

    return evaluate


def async_asker():
    async def evaluate(event):
        return {"result": "ASK", "reason": "late"}

    return evaluate
