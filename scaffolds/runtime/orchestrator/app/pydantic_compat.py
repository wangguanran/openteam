from __future__ import annotations

from copy import deepcopy
import importlib
import inspect
from typing import Any, get_args, get_origin, get_type_hints


try:  # pragma: no cover
    _pydantic = importlib.import_module("pydantic")
    BaseModel = getattr(_pydantic, "BaseModel")
    Field = getattr(_pydantic, "Field")
except Exception:  # pragma: no cover
    _REQUIRED = object()

    class _FieldInfo:
        __slots__ = ("default", "default_factory")

        def __init__(self, *, default: Any = _REQUIRED, default_factory: Any = None) -> None:
            self.default = default
            self.default_factory = default_factory

    def Field(default: Any = _REQUIRED, **kwargs: Any) -> Any:
        default_factory = kwargs.get("default_factory")
        if default is ...:
            default = _REQUIRED
        return _FieldInfo(default=default, default_factory=default_factory)

    def _is_optional(annotation: Any) -> bool:
        origin = get_origin(annotation)
        if origin is None:
            return False
        return any(arg is type(None) for arg in get_args(annotation))

    def _coerce_value(annotation: Any, value: Any) -> Any:
        if annotation is Any or annotation is None or annotation is inspect._empty:
            return value
        if value is None:
            return None
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin in (list, tuple):
            item_type = args[0] if args else Any
            items = list(value or [])
            coerced = [_coerce_value(item_type, item) for item in items]
            return tuple(coerced) if origin is tuple else coerced
        if origin is dict:
            key_type = args[0] if len(args) > 0 else Any
            value_type = args[1] if len(args) > 1 else Any
            return {
                _coerce_value(key_type, k): _coerce_value(value_type, v)
                for k, v in dict(value or {}).items()
            }
        if origin is not None and args:
            if _is_optional(annotation):
                non_none = [arg for arg in args if arg is not type(None)]
                if not non_none:
                    return value
                return _coerce_value(non_none[0], value)
            for arg in args:
                try:
                    return _coerce_value(arg, value)
                except Exception:
                    continue
            return value
        if inspect.isclass(annotation):
            if issubclass(annotation, BaseModel):
                return annotation.model_validate(value)
            if annotation in (str, int, float, bool):
                try:
                    return annotation(value)
                except Exception:
                    return value
        return value

    def _dump_value(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump()
        if isinstance(value, list):
            return [_dump_value(item) for item in value]
        if isinstance(value, tuple):
            return [_dump_value(item) for item in value]
        if isinstance(value, dict):
            return {key: _dump_value(item) for key, item in value.items()}
        return value

    class BaseModel:
        def __init__(self, **data: Any) -> None:
            hints = get_type_hints(self.__class__)
            for name, annotation in hints.items():
                raw_default = getattr(self.__class__, name, _REQUIRED)
                if isinstance(raw_default, _FieldInfo):
                    if raw_default.default_factory is not None:
                        default_value = raw_default.default_factory()
                    elif raw_default.default is not _REQUIRED:
                        default_value = deepcopy(raw_default.default)
                    else:
                        default_value = _REQUIRED
                elif raw_default is not _REQUIRED:
                    default_value = deepcopy(raw_default)
                else:
                    default_value = _REQUIRED

                if name in data:
                    value = data[name]
                elif default_value is not _REQUIRED:
                    value = default_value
                else:
                    raise TypeError(f"missing required field: {name}")
                setattr(self, name, _coerce_value(annotation, value))

            for name, value in data.items():
                if name not in hints:
                    setattr(self, name, value)

        @classmethod
        def model_validate(cls, value: Any) -> "BaseModel":
            if isinstance(value, cls):
                return value
            if isinstance(value, dict):
                return cls(**value)
            if hasattr(value, "model_dump"):
                return cls(**dict(value.model_dump()))
            if hasattr(value, "__dict__"):
                return cls(**dict(value.__dict__))
            raise TypeError(f"cannot validate {cls.__name__} from {type(value).__name__}")

        def model_dump(self) -> dict[str, Any]:
            hints = get_type_hints(self.__class__)
            return {
                name: _dump_value(getattr(self, name))
                for name in hints
                if hasattr(self, name)
            }

        def model_copy(self, *, update: dict[str, Any] | None = None) -> "BaseModel":
            payload = self.model_dump()
            payload.update(dict(update or {}))
            return self.__class__.model_validate(payload)
