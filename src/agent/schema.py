"""Data shapes shared across the pipeline.

`Insight` and `Extraction` are Pydantic models used as the structured-output
schema for the Claude extraction call — the API is constrained to return JSON
matching these shapes, so we get validated objects back instead of parsing free
text. `TASK_TYPES` is the closed set of task categories from the build spec.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# The four task types the spec fixes. Kept as a tuple for reuse in prompts/validation.
TASK_TYPES = ("bug", "feature_request", "polish", "data_problem")

TaskType = Literal["bug", "feature_request", "polish", "data_problem"]

# Human-friendly, proper-cased labels for the raw task_type values above. The raw
# snake_case values stay in the DB / prompts / filters; these are for display only.
TASK_TYPE_LABELS = {
    "bug": "Bug",
    "feature_request": "Feature Request",
    "polish": "Polish",
    "data_problem": "Data Problem",
}


def task_type_label(task_type: str) -> str:
    """Proper-cased display label for a task_type (falls back to a title-cased string)."""
    return TASK_TYPE_LABELS.get(task_type, task_type.replace("_", " ").title())


class Insight(BaseModel):
    """A single distinct insight extracted from one transcript."""

    persona: str = Field(
        description="The role/type of person expressing this (e.g. 'Clinical Data Manager', "
        "'Regulatory Lead', 'Biostatistician'). Use the interviewee's role, not their name."
    )
    task_type: TaskType = Field(
        description="One of: bug (something broken), feature_request (something wanted that "
        "doesn't exist), polish (existing thing that's clunky/slow/confusing), "
        "data_problem (data is wrong, missing, hard to get, or badly formatted)."
    )
    description: str = Field(
        description="One clear sentence stating the need or problem, in neutral product language."
    )
    evidence: str = Field(
        description="A short verbatim quote from the transcript that supports this insight."
    )
    call_date: Optional[str] = Field(
        default=None,
        description="The date the interview/call took place, as YYYY-MM-DD, if it is stated "
        "anywhere in the transcript. Null if the transcript does not state a date.",
    )


class Extraction(BaseModel):
    """The full set of insights extracted from one transcript."""

    insights: List[Insight] = Field(
        description="Every distinct insight found in the transcript. Empty list if none."
    )


class Priority(BaseModel):
    """One ranked priority — a theme engineering/product should act on."""

    title: str = Field(description="Short imperative title, e.g. 'Fix PDF export dropping signatures'.")
    task_type: TaskType = Field(description="The dominant task_type for this theme.")
    rationale: str = Field(
        description="Why it ranks here — reference how often it comes up, the trend, and who's affected."
    )
    affected_personas: List[str] = Field(
        default_factory=list, description="Personas that raised this."
    )
    example_evidence: str = Field(description="One short supporting quote from the insights.")


class PriorityRanking(BaseModel):
    """The ranked top priorities across all insights."""

    priorities: List[Priority] = Field(description="Top priorities, most important first.")
    summary: str = Field(description="One-paragraph overview of the biggest themes.")
