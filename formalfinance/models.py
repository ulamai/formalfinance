from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any
import json


def _fact_id(raw_id: str | None, index: int) -> str:
    return raw_id if raw_id else f"fact-{index:06d}"


@dataclass(frozen=True)
class Context:
    id: str
    period_type: str
    instant: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, ctx_id: str, obj: dict[str, Any]) -> "Context":
        period_type = str(obj.get("period_type", "")).lower()
        if period_type not in {"instant", "duration"}:
            period_type = "instant" if obj.get("instant") else "duration"
        return cls(
            id=ctx_id,
            period_type=period_type,
            instant=obj.get("instant"),
            start_date=obj.get("start_date"),
            end_date=obj.get("end_date"),
            dimensions=dict(obj.get("dimensions", {}) or {}),
        )


@dataclass(frozen=True)
class Fact:
    id: str
    concept: str
    context_id: str
    value: Any
    unit: str | None = None
    decimals: int | str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, obj: dict[str, Any], index: int) -> "Fact":
        return cls(
            id=_fact_id(obj.get("id"), index),
            concept=str(obj["concept"]),
            context_id=str(obj["context_id"]),
            value=obj.get("value"),
            unit=obj.get("unit"),
            decimals=obj.get("decimals"),
            dimensions=dict(obj.get("dimensions", {}) or {}),
            source=dict(obj.get("source", {}) or {}),
        )

    def numeric_value(self) -> float | None:
        if isinstance(self.value, bool):
            return None
        if isinstance(self.value, (int, float)):
            return float(self.value)
        if isinstance(self.value, str):
            if self.unit is None and self.decimals is None:
                return None
            txt = self.value.replace(",", "").strip()
            try:
                return float(txt)
            except ValueError:
                return None
        return None

    def canonical_key(self) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
        dim_pairs = tuple(sorted(self.dimensions.items()))
        return (self.concept, self.context_id, self.unit or "", dim_pairs)


@dataclass(frozen=True)
class Filing:
    accession: str | None
    cik: str | None
    entity: str | None
    period_end: str | None
    taxonomy: str | None
    contexts: dict[str, Context]
    facts: list[Fact]

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Filing":
        contexts_raw = obj.get("contexts", {}) or {}
        contexts = {
            ctx_id: Context.from_dict(ctx_id, ctx_obj)
            for ctx_id, ctx_obj in contexts_raw.items()
        }
        facts_raw = obj.get("facts", []) or []
        facts = [Fact.from_dict(item, i + 1) for i, item in enumerate(facts_raw)]
        return cls(
            accession=obj.get("accession"),
            cik=obj.get("cik"),
            entity=obj.get("entity"),
            period_end=obj.get("period_end"),
            taxonomy=obj.get("taxonomy"),
            contexts=contexts,
            facts=facts,
        )

    def canonical_object(self) -> dict[str, Any]:
        contexts = {
            c.id: {
                "period_type": c.period_type,
                "instant": c.instant,
                "start_date": c.start_date,
                "end_date": c.end_date,
                "dimensions": dict(sorted(c.dimensions.items())),
            }
            for c in sorted(self.contexts.values(), key=lambda x: x.id)
        }
        facts = [
            {
                "id": f.id,
                "concept": f.concept,
                "context_id": f.context_id,
                "unit": f.unit,
                "decimals": f.decimals,
                "value": f.value,
                "dimensions": dict(sorted(f.dimensions.items())),
                "source": dict(sorted(f.source.items())),
            }
            for f in sorted(self.facts, key=lambda x: x.id)
        ]
        return {
            "accession": self.accession,
            "cik": self.cik,
            "entity": self.entity,
            "period_end": self.period_end,
            "taxonomy": self.taxonomy,
            "contexts": contexts,
            "facts": facts,
        }

    def input_digest(self) -> str:
        canonical = json.dumps(self.canonical_object(), sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()
