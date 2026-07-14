"""Extract structured insights from a transcript with one Claude API call.

Uses Claude structured outputs (`messages.parse(output_format=Extraction)`) so
the model is constrained to return JSON matching our Pydantic schema, which the
SDK parses back into a validated `Extraction` object for us. If the first attempt
fails to parse/validate, we retry once with a stricter instruction; a second
failure raises `ExtractionError` so the caller can log the transcript as failed
and continue.
"""

from __future__ import annotations

import anthropic

from .schema import TASK_TYPES, Extraction

SYSTEM_PROMPT = f"""You are an analyst reading the notes/transcript of a customer \
product-feedback call for an enterprise software product serving the pharma vertical \
(commercial analytics — dashboards, account/contract data, 340B/DEA, sales enablement). \
The document is AI-generated meeting notes that typically include a summary, thematic \
sections, decisions, detailed notes, and sometimes a raw transcript. The vendor's team \
runs these calls; the feedback comes from the customer participants.

Extract EVERY distinct product insight expressed by a customer. An insight is a specific \
need, complaint, request, or problem about the product or the customer's workflow. \
Ignore meeting logistics, scheduling, and connection/AV issues (e.g. "Google Meet froze") \
— those are not product insights. Do not invent insights unsupported by the text. Do not \
merge two distinct points into one. If the same point is made twice, record it once.

For each insight, classify `task_type` as exactly one of: {", ".join(TASK_TYPES)}.
  - bug: something in the product is broken or behaves incorrectly
  - feature_request: something wanted that does not exist yet
  - polish: an existing thing that is clunky, slow, confusing, or ugly
  - data_problem: data is wrong, missing, stale, hard to access, or badly formatted

`persona` is the customer participant's ROLE (e.g. "Sales Operations Lead", "Account \
Manager", "Data Analyst"), never their name and never a vendor employee. If the role is \
not stated, infer the best short role label from what they discuss.
`description` is one neutral sentence. `evidence` is a short verbatim quote from the \
document. `call_date` is the call date as YYYY-MM-DD only if stated in the text, else null.

If the document contains no customer product insights, return an empty list."""

STRICTER_SUFFIX = (
    "\n\nIMPORTANT: Return ONLY data that conforms exactly to the required JSON schema. "
    "Every insight MUST have a task_type that is one of the allowed values."
)


class ExtractionError(Exception):
    """Raised when extraction fails even after the stricter retry."""


class FatalExtractionError(ExtractionError):
    """A terminal error (bad key, no access, unknown model) — retrying won't help.

    The caller should stop the whole run rather than mark every transcript failed.
    (Note: a 429 rate-limit is NOT fatal — it's a per-minute/daily cap that may
    clear on its own, so it's left to normal retry handling. The SDK also retries
    429s and 5xxs on its own with backoff.)
    """


def _is_fatal(err: Exception) -> bool:
    """True for errors that will recur on every call (bad key / no access / bad model)."""
    # 401 invalid key, 403 no access to the model, 404 unknown model id.
    return isinstance(
        err,
        (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        ),
    )


def _call(client: anthropic.Anthropic, model: str, max_tokens: int,
          system: str, transcript_text: str) -> Extraction:
    response = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": transcript_text}],
        output_format=Extraction,
    )
    parsed = response.parsed_output
    if not isinstance(parsed, Extraction):
        # None when the model refused or hit max_tokens before valid JSON closed.
        raise ValueError(
            f"Claude returned no schema-valid Extraction (stop_reason={response.stop_reason})."
        )
    return parsed


def extract_insights(
    client: anthropic.Anthropic,
    transcript_text: str,
    *,
    model: str,
    max_tokens: int,
) -> Extraction:
    """Extract insights from one transcript. Retries once with a stricter prompt.

    Raises FatalExtractionError on terminal errors, ExtractionError if both attempts fail.
    """
    try:
        return _call(client, model, max_tokens, SYSTEM_PROMPT, transcript_text)
    except Exception as first_err:  # noqa: BLE001 — retry on any failure, then give up
        if _is_fatal(first_err):
            raise FatalExtractionError(str(first_err)) from first_err
        try:
            return _call(
                client, model, max_tokens,
                SYSTEM_PROMPT + STRICTER_SUFFIX, transcript_text,
            )
        except Exception as second_err:  # noqa: BLE001
            if _is_fatal(second_err):
                raise FatalExtractionError(str(second_err)) from second_err
            raise ExtractionError(
                f"extraction failed twice: first={first_err!r}; retry={second_err!r}"
            ) from second_err
