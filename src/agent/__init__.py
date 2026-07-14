"""Customer Feedback Insights.

An agent-based pipeline that reads a customer-interview transcript library,
extracts structured insights, aggregates them into a persona x task-type needs
matrix, and ranks weekly priorities, surfaced through a Streamlit UI.

Built in stages. Stage 1 (this milestone) is the ingestion pipeline:
loader -> manifest -> Claude extraction -> SQLite store.
"""

__version__ = "0.1.0"
