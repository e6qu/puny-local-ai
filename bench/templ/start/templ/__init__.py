"""A small template language; SPEC.md describes it."""


class TemplateError(ValueError):
    def __init__(self, message, line):
        super().__init__(f"line {line}: {message}")
        self.line = line


def render(source, context):
    raise NotImplementedError("see SPEC.md")
