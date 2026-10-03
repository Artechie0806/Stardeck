"""The dashboard assistant: two agents behind one chat box.

A router (one tiny model call, see router.py) sends each message to:
- the builder: classifies the request and extracts its parts (one schema-constrained call);
  building widgets reuses the widget engine. Proposals are validated against the real schema and
  dashboard before they're shown, and nothing is applied until the user confirms;
- the analyst (analyst.py): answers questions about the data with read-only queries.
Each agent has its own focused prompt.
"""

import re
from typing import Literal

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from stardeck.analyst import analyse
from stardeck.charts import ChartType, Stack
from stardeck.db import DashboardRecord, SourceRecord
from stardeck.llm import LLMClient
from stardeck.query import DateRange, QueryError, UnsafeSQLError, run_widget_query
from stardeck.router import Agent, route
from stardeck.sources import SourceService
from stardeck.widgets import WidgetError, WidgetResult, generate_widget

Intent = Literal["add_widget", "modify_widget", "change_chart_type", "rename_widget", "remove_widget",
                 "define_column", "explain_widget", "clarify", "other"]


class CodeValue(BaseModel):
    code: str
    meaning: str


class ChatIntent(BaseModel):
    intent: Intent
    widget_id: str | None = Field(None, description="Existing widget the message refers to, if any.")
    request: str | None = Field(None, description="add_widget: what to chart. modify_widget: the change wanted.")
    chart_type: ChartType | None = None
    stack: Stack | None = Field(None, description="change_chart_type: stacked, percent (100%) or none.")
    new_title: str | None = None
    table: str | None = None
    column: str | None = None
    description: str | None = Field(None, description="define_column: plain meaning of the column.")
    unit: str | None = None
    values: list[CodeValue] = Field(default_factory=list)
    reply: str = Field("", description="clarify/other/explain: a short, friendly answer for the user.")
    options: list[str] = Field(default_factory=list, max_length=4,
                               description="clarify: 2-4 short answers the user can tap, e.g. 'By month'.")


PROMPT = """You are the assistant inside a dashboard tool for non-technical users. Classify the user's
latest message and extract its details. You never change anything yourself; the app shows the
user a proposal to confirm.

Intents:
- add_widget: wants a new chart, number or table. `request` = precise description of it.
  A bare phrase naming data ("Orders by status", "revenue per month", "top customers") is add_widget.
- modify_widget: wants an existing widget's data changed (filter, top N, different measure...).
  Set widget_id and `request` = the change.
- change_chart_type: wants a different chart type for a widget. Set widget_id and chart_type
  (bar = vertical columns, hbar = horizontal bars; also pie, donut, line, area, treemap, funnel,
  waterfall, scatter, bubble, heatmap, matrix, radar, sankey, histogram, boxplot, gauge, table).
  "Stacked" or "100%" also sets stack.
- rename_widget: set widget_id and new_title.
- remove_widget: set widget_id.
- define_column: explains what a column or its codes mean, e.g. "sts 9 means refunded". Set
  table, column, description, unit and values (code -> meaning) as stated by the user.
- explain_widget: asks what a widget shows or how it's calculated. Set widget_id, and `reply`.
- clarify: the request leaves a choice open that changes the result, so ask ONE short question and
  give 2-4 tappable `options`. Ask when:
  * no data is named ("I need a chart", "show me something") -> what to show;
  * a comparison or difference has no breakdown ("year on year difference", "compare sales")
    -> one total number, month by month, or by a category;
  * a word could mean different measures ("sales": revenue or number of orders?);
  * it's unclear which widget is meant.
  Don't ask when the request names what to measure and how to break it down ("revenue by month",
  "top 5 products by units"); just build it.
- other: anything else; `reply` briefly says what you can help with.

If the conversation shows the user is answering your question, combine their answer with their
earlier request into one complete `request` and act on it (don't ask again).

Dashboard widgets (id | type | title):
{widgets}

Tables and columns (* = meaning unknown to the app):
{columns}"""


def _columns_text(src: SourceService, rec: SourceRecord) -> str:
    out = src.to_out(rec)
    lines = []
    for t in out.tables:
        cols = [f"{c.name}{'*' if c.flag else ''}" for c in t.columns if not (c.definition or {}).get("hidden")]
        lines.append(f"{t.name}({', '.join(cols)})")
    return "\n".join(lines)


def _widget_payload(w: WidgetResult) -> dict:
    return {
        "title": w.title, "summary": w.summary, "sql": w.sql, "spec": w.spec,
        "dateFilter": {"table": w.date_filter[0], "column": w.date_filter[1]} if w.date_filter else None,
    }


def _unknown_columns_used(sql: str, dialect: str, src: SourceService, rec: SourceRecord) -> list[str]:
    """Flagged, undefined columns the query relies on, so the assistant can ask about them."""
    try:
        used = {c.name.lower() for c in sqlglot.parse_one(sql, read=dialect).find_all(exp.Column)}
    except sqlglot.errors.SqlglotError:
        return []
    out = src.to_out(rec)
    return [f"{t.name}.{c.name}" for t in out.tables for c in t.columns
            if c.flag and c.definition is None and c.name.lower() in used]


_COMPARISON = re.compile(
    r"year[\s-]*(on|over|to)[\s-]*year|\byoy\b|\bmom\b|month[\s-]*(on|over)[\s-]*month|\bcompar|"
    r"\bdifference\b|\bvs\.?\b|\bversus\b|\bgrowth\b|\bchange\b", re.IGNORECASE)
_BREAKDOWN = re.compile(
    r"\b(by|per|each|every|across|monthly|weekly|daily|quarterly|yearly|trend|over time|total|overall|"
    r"one number|single number|month by month|kpi)\b", re.IGNORECASE)
_MEASURE_WORDS = {"revenue", "sales", "orders", "order", "customers", "customer", "count", "amount", "profit",
                  "units", "quantity", "price", "value", "spend", "income", "cost", "users", "signups", "rate"}


def vague_request(request: str, vocabulary: set[str]) -> tuple[str, list[str]] | None:
    """A question to ask when a chart request leaves a key choice open, else None.

    Backstop for when the model goes ahead anyway; mirrors the cases listed in the prompt.
    """
    words = {w for w in re.findall(r"[a-z0-9]+", request.lower())}
    words |= {w[:-1] for w in words if w.endswith("s")}
    if not words & (vocabulary | _MEASURE_WORDS):
        return "What should it show?", []
    if _COMPARISON.search(request) and not _BREAKDOWN.search(request):
        return ("Should I show that as one total number, or broken down?",
                ["One total number", "Month by month (this year vs last year)", "By category"])
    return None


class Assistant:
    def __init__(self, sources: SourceService):
        self.sources = sources

    def _vocabulary(self, src: SourceRecord) -> set[str]:
        out = self.sources.to_out(src)
        vocab: set[str] = set()
        for t in out.tables:
            vocab |= set(re.findall(r"[a-z0-9]+", t.name.lower()))
            for c in t.columns:
                vocab |= set(re.findall(r"[a-z0-9]+", c.name.lower()))
                vocab |= set(re.findall(r"[a-z]{3,}", ((c.definition or {}).get("description") or "").lower()))
        for g in out.glossary:
            vocab |= set(re.findall(r"[a-z0-9]+", g.term.lower()))
        return {v for v in vocab if len(v) > 2}

    def _suggestions(self, src: SourceRecord) -> list[str]:
        """Concrete starting points for "what should it show?", from the glossary and main tables."""
        out = self.sources.to_out(src)
        opts = [f"{g.term.capitalize()} by month" for g in out.glossary[:2]]
        biggest = sorted((t for t in out.tables if t.row_count), key=lambda t: -(t.row_count or 0))
        opts += [f"Number of {t.name.replace('_', ' ')} by month" for t in biggest[:2]]
        return opts[:4]

    def respond(self, llm: LLMClient, dash: DashboardRecord, src: SourceRecord, message: str,
                history: list[dict], rng: DateRange | None, compare: DateRange | None,
                pending: str | None = None, agent: Agent | None = None, basis_sql: str | None = None) -> dict:
        """Route the message to the builder or the analyst; the reply says which one answered.

        `pending` (the builder's open question) and `agent` (set by a button, e.g. "Add as chart")
        already decide the agent; otherwise the router model does.
        """
        if not agent:
            agent = "builder" if pending else route(llm, message, history, [w["title"] for w in dash.widgets or []])
        if agent == "analyst":
            return {**self._analyse(llm, src, message, history, rng), "agent": "analyst"}
        if basis_sql:  # a chart of an analyst answer: start from its checked query, skip classification
            request = (f"{message}\nStart from this query; it already returns the right numbers. Keep its "
                       f"filters and logic, and only reshape it for the chart:\n{basis_sql}")
            return {**self._build(llm, src, request, rng, compare, None), "agent": "builder"}
        return {**self._builder(llm, dash, src, message, history, rng, compare, pending), "agent": "builder"}

    def _analyse(self, llm: LLMClient, src: SourceRecord, question: str, history: list[dict],
                 rng: DateRange | None) -> dict:
        svc = self.sources
        a = analyse(llm, svc.engine(src), svc.dialect(src), svc.tables(src), svc.effective_definitions(src),
                    question, history, rng, prepared=svc.prepared(src), timeout_s=svc.settings.query_timeout_s)
        out: dict = {"text": a.answer, "analysis": {
            "queries": a.queries,
            "evidence": {"columns": a.evidence.columns, "rows": a.evidence.rows, "truncated": a.evidence.truncated}
            if a.evidence else None,
            "chartRequest": a.chart_request,
        }}
        if a.follow_ups:
            out["options"] = a.follow_ups
        return out

    def _builder(self, llm: LLMClient, dash: DashboardRecord, src: SourceRecord, message: str,
                 history: list[dict], rng: DateRange | None, compare: DateRange | None,
                 pending: str | None = None) -> dict:
        """`pending` is the request the assistant last asked a question about; the message answers it."""
        answered = bool(pending)
        if pending:
            message = f"{pending}\n(My answer to your question: {message})"
        widgets = dash.widgets or []
        widget_lines = "\n".join(f"{w['id']} | {w['spec']['type']} | {w['title']}" for w in widgets) or "(none yet)"
        messages = [{"role": "system", "content": PROMPT.format(widgets=widget_lines,
                                                               columns=_columns_text(self.sources, src))}]
        for h in history[-6:]:
            if h.get("role") in ("user", "assistant") and h.get("text"):
                messages.append({"role": h["role"], "content": str(h["text"])[:1000]})
        messages.append({"role": "user", "content": message})
        intent = llm.structured(messages, ChatIntent)
        by_id = {w["id"]: w for w in widgets}
        target = by_id.get(intent.widget_id or "") or _match_title(message, widgets)

        if intent.intent == "clarify" and answered:
            # Never ask twice in a row: build with the original request plus the user's answer.
            intent.intent, intent.request = "add_widget", message
        if answered and intent.intent == "modify_widget" and not _match_title(pending or "", widgets):
            # The question was about a new chart; the answer's words may resemble an existing title.
            intent.intent = "add_widget"
        if intent.intent == "clarify":
            options = [o.strip().rstrip("?").strip() for o in intent.options if o.strip().rstrip("?").strip()][:4]
            text = intent.reply.strip() or ("Which of these did you mean?" if options
                                             else "Could you say a bit more about what you'd like to see?")
            return {"text": text, "options": options, "pending": message}

        match intent.intent:
            case "add_widget":
                request = intent.request or message
                if not answered and (question := vague_request(message, self._vocabulary(src))):
                    text, options = question
                    return {"text": text, "options": options or self._suggestions(src), "pending": message}
                return self._build(llm, src, request, rng, compare, None)
            case "modify_widget" if target:
                change = intent.request or message
                req = (f"Rebuild the widget \"{target['title']}\" ({target['summary']}). Its current SQL is:\n"
                       f"{target['sql']}\nApply this change: {change}")
                return self._build(llm, src, req, rng, compare, target)
            case "change_chart_type" if target and (intent.chart_type or intent.stack):
                chart, stack_word = _chart_words(message, intent.chart_type or target["spec"]["type"])
                intent.stack = stack_word or intent.stack
                single = ("kpi", "gauge")
                if (target["spec"]["type"] in single) != (chart in single):
                    return {"text": "Single-number tiles and charts can't be swapped directly. Ask me to rebuild it, "
                                    "e.g. \"show it by month\"."}
                stack = intent.stack or "none"
                label = {"bar": "column", "hbar": "bar"}.get(chart, chart)
                if stack != "none":
                    label = f"{'100% ' if stack == 'percent' else ''}stacked {label}"
                proposal = {"kind": "change_type", "widgetId": target["id"], "widgetTitle": target["title"],
                            "type": chart}
                if stack != "none" or target["spec"].get("stack"):
                    proposal["stack"] = stack
                return {"text": f"I'll show **{target['title']}** as a {label} chart.", "proposal": proposal}
            case "rename_widget" if target and intent.new_title:
                return {"text": f"Rename **{target['title']}**?",
                        "proposal": {"kind": "rename_widget", "widgetId": target["id"],
                                     "widgetTitle": target["title"], "title": intent.new_title.strip()[:80]}}
            case "remove_widget" if target:
                return {"text": f"Remove **{target['title']}** from the dashboard?",
                        "proposal": {"kind": "remove_widget", "widgetId": target["id"], "widgetTitle": target["title"]}}
            case "define_column":
                return self._define(src, intent)
            case "explain_widget" if target:
                when = ("It follows the dashboard's date filter on "
                        f"{target['dateFilter']['table']}.{target['dateFilter']['column']}."
                        if target.get("dateFilter") else "It always covers all dates.")
                extra = f"\n\n{intent.reply.strip()}" if intent.reply.strip() else ""
                return {"text": f"**{target['title']}**: {target['summary']} {when} "
                                f"Use *View SQL* on the widget to see the exact query.{extra}"}
            case "modify_widget" | "change_chart_type" | "rename_widget" | "remove_widget" | "explain_widget":
                return {"text": "Which widget do you mean? You can use its title, e.g. "
                                f"\"{widgets[0]['title'] if widgets else 'Revenue by month'}\"."}
        return {"text": intent.reply.strip() or (
            "I can add charts, change or remove widgets, and remember what columns mean. "
            "Try \"Revenue by month\" or \"sts 9 means refunded\".")}

    def _build(self, llm, src, request, rng, compare, replacing: dict | None) -> dict:
        svc = self.sources
        engine, dialect = svc.engine(src), svc.dialect(src)
        try:
            w = generate_widget(llm, engine, dialect, svc.tables(src), svc.effective_definitions(src), request,
                                prepared=svc.prepared(src),
                                timeout_s=svc.settings.query_timeout_s)
        except WidgetError as exc:
            return {"text": f"I couldn't build that. {exc}"}
        payload = _widget_payload(w)
        preview = {"columns": w.result.columns, "rows": w.result.rows, "truncated": w.result.truncated}
        if w.date_filter and rng:  # preview with the dashboard's current date range
            try:
                r = run_widget_query(engine, w.sql, dialect, kpi=w.spec["type"] == "kpi", date_filter=w.date_filter,
                                     prepared=svc.prepared(src),
                                     rng=rng, compare=compare, timeout_s=svc.settings.query_timeout_s)
                preview = {"columns": r.columns, "rows": r.rows, "truncated": r.truncated}
            except (QueryError, UnsafeSQLError):
                pass
        payload["result"] = preview
        notes = " ".join(w.notes)
        unknown = _unknown_columns_used(w.sql, dialect, svc, src)
        ask = (f"\n\nI wasn't sure what **{unknown[0]}** means, so I went by its sample values. "
               "Tell me what it means and I'll remember it.") if unknown else ""
        if replacing:
            text = f"Here's the updated **{replacing['title']}**. Replace the current version?"
            proposal = {"kind": "replace_widget", "widgetId": replacing["id"], "widgetTitle": replacing["title"],
                        "widget": payload}
        else:
            text = f"Here's **{w.title}**. Add it to the dashboard?"
            proposal = {"kind": "add_widget", "widget": payload}
        return {"text": text + (f" ({notes})" if notes else "") + ask, "proposal": proposal}

    def _define(self, src: SourceRecord, intent: ChatIntent) -> dict:
        out = self.sources.to_out(src)
        col_name = (intent.column or "").strip().lower()
        matches = [(t.name, c.name) for t in out.tables for c in t.columns
                   if c.name.lower() == col_name and (not intent.table or t.name.lower() == intent.table.lower())]
        if not matches:  # the model may have guessed the table wrong; fall back to any table
            matches = [(t.name, c.name) for t in out.tables for c in t.columns if c.name.lower() == col_name]
        if not matches:
            return {"text": f"I couldn't find a column called \"{intent.column}\". Check the name on the "
                            "Definitions page."}
        table, column = matches[0]
        definition = {k: v for k, v in {
            "description": (intent.description or "").strip() or None,
            "unit": (intent.unit or "").strip() or None,
            "values": {v.code.strip(): v.meaning.strip() for v in intent.values if v.code.strip()} or None,
        }.items() if v}
        if not definition:
            return {"text": f"What does **{table}.{column}** mean? For example: \"{column} is the order status\"."}
        return {"text": f"Got it. Here's how I'd record **{table}.{column}**. Every chart that uses it will "
                        "follow this.",
                "proposal": {"kind": "definition", "table": table, "column": column, "definition": definition}}


def _chart_words(message: str, chart: str) -> tuple[str, str | None]:
    """Power BI vocabulary is precise, so read it from the message rather than trust the model:
    "bar" = horizontal, "column" = vertical, "100%" = percent-stacked, "stacked" = stacked."""
    m = message.lower()
    stack = "percent" if re.search(r"100\s*%|percent", m) else "stacked" if "stack" in m else None
    if chart in ("bar", "hbar"):
        if re.search(r"\bcolumns?\b", m):
            chart = "bar"
        elif re.search(r"\bbars?\b", m) and not re.search(r"\bvertical\b", m):
            chart = "hbar"
    return chart, stack


def _match_title(message: str, widgets: list[dict]) -> dict | None:
    """Fallback when the model didn't give a widget id: best title word overlap."""
    msg = message.lower()
    best, best_score = None, 0.0
    for w in widgets:
        words = [x for x in re.findall(r"[a-z0-9]+", w["title"].lower()) if len(x) > 2]
        if words:
            score = sum(x in msg for x in words) / len(words)
            if score > best_score:
                best, best_score = w, score
    return best if best_score >= 0.5 else None
