"""Enterprise bulk upload engine: templates, parse, validate, duplicate checks, save."""
from core.bulk_upload.base import DATASETS, Dataset, get_dataset, register

# Register all built-in datasets on import.
from core.bulk_upload import datasets as _datasets  # noqa: F401

__all__ = ['DATASETS', 'Dataset', 'get_dataset', 'register']
