"""Dataset registry and validation report structures for bulk upload."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID


def _json_safe(value: Any) -> Any:
    """Make nested structures session/JSON serializable (UUID, Decimal, dates)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


@dataclass
class Column:
    key: str
    required: bool = False
    help: str = ''


@dataclass
class RowOutcome:
    row_number: int
    status: str  # ready | duplicate | error | skipped
    message: str = ''
    preview: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return _json_safe(asdict(self))


@dataclass
class ValidationReport:
    dataset_key: str
    headers_ok: bool
    missing_headers: list[str]
    outcomes: list[RowOutcome]
    ready_count: int = 0
    duplicate_count: int = 0
    error_count: int = 0

    def summarize(self):
        self.ready_count = sum(1 for o in self.outcomes if o.status == 'ready')
        self.duplicate_count = sum(1 for o in self.outcomes if o.status == 'duplicate')
        self.error_count = sum(1 for o in self.outcomes if o.status == 'error')
        return self

    def to_session(self):
        self.summarize()
        return {
            'dataset_key': self.dataset_key,
            'headers_ok': self.headers_ok,
            'missing_headers': list(self.missing_headers),
            'ready_count': self.ready_count,
            'duplicate_count': self.duplicate_count,
            'error_count': self.error_count,
            'outcomes': [o.to_dict() for o in self.outcomes],
            'payloads': [_json_safe(o.payload) for o in self.outcomes if o.status == 'ready'],
        }


@dataclass
class Dataset:
    key: str
    label: str
    description: str
    columns: list[Column]
    sample_rows: list[dict]
    validate_rows: Callable
    apply_rows: Callable

    @property
    def headers(self) -> list[str]:
        return [c.key for c in self.columns]

    @property
    def required_headers(self) -> list[str]:
        return [c.key for c in self.columns if c.required]


DATASETS: dict[str, Dataset] = {}


def register(dataset: Dataset):
    DATASETS[dataset.key] = dataset
    return dataset


def get_dataset(key: str) -> Dataset:
    if key not in DATASETS:
        raise KeyError(key)
    return DATASETS[key]
