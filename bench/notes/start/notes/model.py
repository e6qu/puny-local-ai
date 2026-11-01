from dataclasses import dataclass, field
from datetime import date
from typing import List


@dataclass
class Note:
    title: str
    body: str
    created: date
    tags: List[str] = field(default_factory=list)
