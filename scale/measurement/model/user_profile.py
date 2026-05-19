from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class UserProfile:
    name: str
    sex: str
    height_cm: int
    birth_date: date

    def age_at(self, when: date) -> int:
        years = when.year - self.birth_date.year
        if (when.month, when.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years
