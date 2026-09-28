"""The evaluation dataset format (evaluation/dataset.json).

Relevance is labelled with *evidence snippets* rather than chunk IDs: a retrieved chunk
is relevant when it comes from the named document and contains the evidence text. Chunk
IDs change whenever the chunk size, overlap or extractor changes; evidence snippets
don't, so the same labels evaluate every configuration.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Category = Literal["factual", "paraphrase", "keyword", "multi_hop", "unanswerable"]


class Evidence(BaseModel):
    document: str = Field(min_length=1, description="Filename in the corpus")
    evidence: str = Field(min_length=3, description="Text a relevant chunk must contain")


class EvalQuestion(BaseModel):
    id: str = Field(min_length=1)
    category: Category
    question: str = Field(min_length=3)
    expected_answer: str
    # Each fact is a list of accepted phrasings; a correct answer states every fact.
    expected_facts: list[list[str]] = Field(default_factory=list)
    relevant: list[Evidence] = Field(default_factory=list)

    @property
    def answerable(self) -> bool:
        return bool(self.relevant)

    @field_validator("expected_facts")
    @classmethod
    def _facts_have_variants(cls, facts: list[list[str]]) -> list[list[str]]:
        if any(not variants or any(not v.strip() for v in variants) for variants in facts):
            raise ValueError("every expected fact needs at least one non-empty phrasing")
        return facts

    @model_validator(mode="after")
    def _consistent(self) -> "EvalQuestion":
        if self.category == "unanswerable" and (self.relevant or self.expected_facts):
            raise ValueError(f"{self.id}: unanswerable questions have no relevant evidence or facts")
        if self.category != "unanswerable" and not (self.relevant and self.expected_facts):
            raise ValueError(f"{self.id}: answerable questions need relevant evidence and expected facts")
        return self


class EvalDataset(BaseModel):
    name: str
    version: int
    description: str = ""
    corpus: str = Field(description="Corpus directory, relative to the dataset file")
    questions: list[EvalQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "EvalDataset":
        duplicates = [qid for qid, n in Counter(q.id for q in self.questions).items() if n > 1]
        if duplicates:
            raise ValueError(f"duplicate question ids: {', '.join(duplicates)}")
        return self


def load_dataset(path: Path) -> tuple[EvalDataset, Path]:
    """The dataset and its corpus directory. Every labelled document must exist in the corpus."""
    dataset = EvalDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
    corpus = (path.parent / dataset.corpus).resolve()
    if not corpus.is_dir():
        raise ValueError(f"corpus directory not found: {corpus}")
    missing = sorted(
        {e.document for q in dataset.questions for e in q.relevant if not (corpus / e.document).is_file()}
    )
    if missing:
        raise ValueError(f"documents referenced by the dataset are missing from the corpus: {missing}")
    return dataset, corpus
