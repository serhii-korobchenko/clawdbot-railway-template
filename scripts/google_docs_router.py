#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import traceback
import urllib.request


SCRIPT_DIR = "/data/workspace/scripts"
ROUTER_LOG = "/data/workspace/google-docs/router_calls.log"

SCRIPTS = {
    "create": f"{SCRIPT_DIR}/google_docs_create.py",
    "read": f"{SCRIPT_DIR}/google_docs_read.py",
    "append": f"{SCRIPT_DIR}/google_docs_append.py",
    "replace": f"{SCRIPT_DIR}/google_docs_replace.py",
    "format": f"{SCRIPT_DIR}/google_docs_format.py",
    "table": f"{SCRIPT_DIR}/google_docs_table.py",
    "markdown_cleanup": f"{SCRIPT_DIR}/google_docs_markdown_cleanup.py",
}


def log_router_call(task):
    try:
        os.makedirs(os.path.dirname(ROUTER_LOG), exist_ok=True)
        with open(ROUTER_LOG, "a", encoding="utf-8") as f:
            f.write(str(task).strip() + "\n")
    except Exception:
        pass


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False))


def run_json(cmd):
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed: "
            + " ".join(cmd)
            + "\nSTDOUT:\n"
            + (result.stdout or "")
            + "\nSTDERR:\n"
            + (result.stderr or "")
        )

    raw = (result.stdout or "").strip()

    try:
        return json.loads(raw)
    except Exception:
        raise RuntimeError("Invalid JSON from command: " + " ".join(cmd) + "\nOUTPUT:\n" + raw)


def extract_quoted(text):
    return re.findall(r'"([^"]+)"', text)


def extract_doc_id_or_url(text):
    url_match = re.search(
        r"https://docs\.google\.com/document/d/[a-zA-Z0-9_-]+(?:/edit)?",
        text,
    )
    if url_match:
        return url_match.group(0)

    id_match = re.search(r"\b[a-zA-Z0-9_-]{30,}\b", text)
    if id_match:
        return id_match.group(0)

    return None


def extract_text_after_label(text, labels):
    lower = text.lower()

    for label in labels:
        idx = lower.find(label.lower())
        if idx >= 0:
            return text[idx + len(label):].strip()

    return ""


def is_research_report_task(task):
    t = task.lower()

    triggers = [
        "з відкритих джерел",
        "відкритих джерел",
        "open sources",
        "open-source",
        "osint",
        "знайди інформацію",
        "знайти інформацію",
        "знайди дані",
        "проведи дослідження",
        "проаналізуй",
        "аналіз",
        "аналітичний",
        "аналітична",
        "порівняльні таблиці",
        "порівнядьні таблиці",
        "порівняльн",
        "джерела",
        "sources",
        "source-backed",
        "research",
    ]

    return any(x in t for x in triggers)


def is_military_topic(task):
    t = task.lower()

    markers = [
        "збройних сил",
        "зсу",
        "росі",
        "війні",
        "війна",
        "армі",
        "військ",
        "сил і засобів",
        "бойов",
        "озброєн",
        "defense",
        "military",
        "armed forces",
        "russia",
        "ukraine",
        "war",
    ]

    return any(x in t for x in markers)


def tavily_search(query, max_results=8):
    api_key = os.getenv("TAVILY_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is missing")

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
    }

    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def openai_compose_report(task, search_data):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")

    sources = []

    for r in search_data.get("results", [])[:10]:
        sources.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": (r.get("content", "") or "")[:2500],
                "score": r.get("score", None),
            }
        )

    military_guardrails = ""

    if is_military_topic(task):
        military_guardrails = (
            "\nFor military topics, keep the report at a high, public, non-operational level. "
            "Do not include live positions, current tactical locations, routes, unit vulnerabilities, "
            "targeting advice, instructions for force employment, or operational recommendations. "
            "Use only generalized open-source information and explicitly state uncertainty."
        )

    system = (
        "You are an OSINT research assistant preparing Ukrainian-language Google Docs reports. "
        "Use only the supplied open-source search snippets and URLs. "
        "If evidence is insufficient, say so explicitly. "
        "Return clean Markdown only. "
        "Do not invent citations or facts. "
        "Include a source list with URLs."
        + military_guardrails
    )

    user_payload = {
        "task": task,
        "search_answer": search_data.get("answer", ""),
        "sources": sources,
        "required_structure": [
            "Title",
            "Short executive summary",
            "Scope and limitations",
            "Comparative tables where supported by sources",
            "Analytical observations",
            "Source list with URLs",
            "Conclusions",
        ],
    }

    payload = {
        "model": os.getenv("OPENAI_REPORT_MODEL", "gpt-4.1-mini"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return data["choices"][0]["message"]["content"].strip()


def build_research_report_text(task, title):
    search_data = tavily_search(task, max_results=8)
    report = openai_compose_report(task, search_data)

    if not report.lstrip().startswith("#"):
        report = "# " + title + "\n\n" + report

    return report


def parse_create_task(task):
    quoted = extract_quoted(task)
    title = quoted[0] if quoted else "Untitled Google Doc"

    text = ""

    # Prefer explicit "Текст:" / "Text:" block.
    m = re.search(r"(?:Текст|Text)\s*:\s*(.*)", task, flags=re.S | re.I)
    if m:
        text = m.group(1).strip()

        # Remove trailing instruction fragments.
        text = re.sub(r'\n?\s*Зроби\s+".*$', "", text, flags=re.S)
        text = re.sub(r"\n?\s*Поверни\s+(?:raw\s+)?URL\.?.*$", "", text, flags=re.S | re.I)

    if not text:
        if is_research_report_task(task):
            text = build_research_report_text(task, title)
            return title, text

        t = task.lower()

        if any(
            x in t
            for x in [
                "репорт",
                "report",
                "аналітич",
                "analytical",
                "structured document",
                "briefing",
                "cv",
            ]
        ):
            if "україн" in t or "українською" in t:
                text = f"""# {title}

## Коротке резюме
Цей документ створено як короткий структурований тестовий репорт у Google Docs. Мета документа — перевірити високорівневий workflow створення репортів через OpenClaw.

## Вступ і мета
Метою тесту є перевірка того, що запит на створення репорту не імітується моделлю, а реально передається до локального Google Docs router та створює документ із raw URL.

## Таблиця / структуровані результати

| № | Елемент документа | Статус |
|---|-------------------|--------|
| 1 | Назва | Додано |
| 2 | Коротке резюме | Додано |
| 3 | Висновки | Додано |

## Висновки
Тестовий документ має підтвердити, що базовий сценарій створення Google Doc через OpenClaw працює коректно. Наступний етап — додати повноцінне форматування та окрему вставку таблиць через Google Docs API.
"""
            else:
                text = f"""# {title}

## Executive summary
This document was created as a short structured Google Docs report to test the high-level OpenClaw report creation workflow.

## Introduction and purpose
The purpose of this test is to verify that the report creation request is executed through the local Google Docs router and returns a real raw URL.

## Structured results

| # | Document element | Status |
|---|------------------|--------|
| 1 | Title | Added |
| 2 | Executive summary | Added |
| 3 | Conclusions | Added |

## Conclusions
This test document should confirm that the basic Google Doc creation workflow works correctly through OpenClaw.
"""
        else:
            text = title

    return title, text


def parse_format_instruction(task):
    quoted = extract_quoted(task)
    target = None

    if len(quoted) >= 2:
        target = quoted[1]
    elif len(quoted) == 1:
        target = quoted[0]

    if not target:
        raise RuntimeError("Could not determine target text for formatting.")

    t = task.lower()
    opts = []

    if "heading 1" in t or "heading_1" in t:
        opts.append("heading=HEADING_1")
    elif "heading 2" in t or "heading_2" in t:
        opts.append("heading=HEADING_2")
    elif "heading 3" in t or "heading_3" in t:
        opts.append("heading=HEADING_3")

    if "жирн" in t or "bold" in t:
        opts.append("bold=true")

    if "курсив" in t or "italic" in t:
        opts.append("italic=true")

    if "підкрес" in t or "underline" in t:
        opts.append("underline=true")

    if "по центру" in t or "center" in t or "центру" in t:
        opts.append("alignment=CENTER")

    size_match = re.search(r"(?:розмір|font\s*size)\s*(\d+)", t)
    if size_match:
        opts.append("fontSize=" + size_match.group(1))

    if not opts:
        opts.append("bold=true")

    return target, ",".join(opts)


def handle_create_or_create_format(task):
    title, text = parse_create_task(task)

    created = run_json(
        [
            "python3",
            SCRIPTS["create"],
            title,
            text,
        ]
    )

    doc_id = created.get("document_id") or created.get("documentId")
    url = created.get("url")

    result = {
        "ok": bool(created.get("ok", True)),
        "operation": "create_or_create_format",
        "title": title,
        "document_id": doc_id,
        "url": url,
        "steps": {"create": created},
    }
    if doc_id:
        try:
            cleanup = run_json(["python3", SCRIPTS["markdown_cleanup"], doc_id])
            result["steps"]["markdown_cleanup"] = cleanup
        except Exception as err:
            result["steps"]["markdown_cleanup_error"] = str(err)

    # Best-effort formatting for title / headings.
    if doc_id and ("формат" in task.lower() or "format" in task.lower()):
        try:
            target, opts = parse_format_instruction(task)
            formatted = run_json(["python3", SCRIPTS["format"], doc_id, target, opts])
            result["steps"]["format"] = formatted
        except Exception as err:
            result["steps"]["format_error"] = str(err)

    return result


def handle_read(task):
    doc_ref = extract_doc_id_or_url(task)

    if not doc_ref:
        raise RuntimeError("No Google Doc ID or URL found for read operation.")

    read = run_json(["python3", SCRIPTS["read"], doc_ref])

    return {
        "ok": bool(read.get("ok", True)),
        "operation": "read",
        "title": read.get("title"),
        "document_id": read.get("document_id") or read.get("documentId"),
        "url": read.get("url"),
        "first_500_chars": read.get("first_500_chars") or read.get("text", "")[:500],
        "steps": {"read": read},
    }


def handle_append(task):
    doc_ref = extract_doc_id_or_url(task)

    if not doc_ref:
        raise RuntimeError("No Google Doc ID or URL found for append operation.")

    m = re.search(r"(?:Текст|Text)\s*:\s*(.*)", task, flags=re.S | re.I)
    append_text = m.group(1).strip() if m else ""

    if not append_text:
        quoted = extract_quoted(task)
        append_text = quoted[-1] if quoted else ""

    if not append_text:
        raise RuntimeError("No text found for append operation.")

    appended = run_json(["python3", SCRIPTS["append"], doc_ref, append_text])

    return {
        "ok": bool(appended.get("ok", True)),
        "operation": "append",
        "document_id": appended.get("document_id") or appended.get("documentId"),
        "url": appended.get("url"),
        "steps": {"append": appended},
    }


def handle_replace(task):
    doc_ref = extract_doc_id_or_url(task)

    if not doc_ref:
        raise RuntimeError("No Google Doc ID or URL found for replace operation.")

    quoted = extract_quoted(task)

    if len(quoted) >= 2:
        search_text = quoted[-2]
        replace_text = quoted[-1]
    else:
        raise RuntimeError(
            'Could not determine search_text and replace_text. Use: replace "old text" with "new text".'
        )

    replaced = run_json(
        [
            "python3",
            SCRIPTS["replace"],
            doc_ref,
            search_text,
            replace_text,
        ]
    )

    return {
        "ok": bool(replaced.get("ok", True)),
        "operation": "replace",
        "document_id": replaced.get("document_id") or replaced.get("documentId"),
        "url": replaced.get("url"),
        "steps": {"replace": replaced},
    }


def handle_format(task):
    doc_ref = extract_doc_id_or_url(task)

    if not doc_ref:
        raise RuntimeError("No Google Doc ID or URL found for format operation.")

    target, opts = parse_format_instruction(task)
    formatted = run_json(["python3", SCRIPTS["format"], doc_ref, target, opts])

    return {
        "ok": bool(formatted.get("ok", True)),
        "operation": "format",
        "document_id": formatted.get("document_id") or formatted.get("documentId"),
        "url": formatted.get("url"),
        "steps": {"format": formatted},
    }


def parse_table_from_task(task):
    m = re.search(r"(?:Таблиця|Table)\s*:\s*(.*)", task, flags=re.S | re.I)
    raw = m.group(1).strip() if m else ""

    if not raw:
        raise RuntimeError("No table data found. Use 'Таблиця:' or 'Table:'.")

    rows = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if set(line) <= set("|-: "):
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
        else:
            cells = [c.strip() for c in re.split(r"\s{2,}|\t", line)]
        rows.append(cells)

    if not rows:
        raise RuntimeError("Could not parse table rows.")

    return rows


def handle_table(task):
    doc_ref = extract_doc_id_or_url(task)

    if not doc_ref:
        raise RuntimeError("No Google Doc ID or URL found for table operation.")

    rows = parse_table_from_task(task)
    table_json = json.dumps(rows, ensure_ascii=False)

    table = run_json(["python3", SCRIPTS["table"], doc_ref, table_json])

    return {
        "ok": bool(table.get("ok", True)),
        "operation": "table",
        "document_id": table.get("document_id") or table.get("documentId"),
        "url": table.get("url"),
        "steps": {"table": table},
    }


def route(task):
    t = task.lower()

    has_doc_ref = bool(extract_doc_id_or_url(task))

    if has_doc_ref and any(x in t for x in ["прочитай", "read", "поверни назву", "title"]):
        return handle_read(task)

    if has_doc_ref and any(x in t for x in ["заміни", "replace"]):
        return handle_replace(task)

    if has_doc_ref and any(x in t for x in ["додай таблиц", "add table", "таблиця:", "table:"]):
        return handle_table(task)

    if has_doc_ref and any(x in t for x in ["додай", "append", "встав", "insert"]):
        return handle_append(task)

    if has_doc_ref and any(x in t for x in ["формат", "format", "жирн", "bold", "розмір"]):
        return handle_format(task)

    if any(x in t for x in ["створи", "create", "новий документ", "google doc", "репорт", "report", "briefing", "cv"]):
        return handle_create_or_create_format(task)

    # Default behavior: create a document from the request text.
    return handle_create_or_create_format(task)


def main():
    if len(sys.argv) < 2:
        emit(
            {
                "ok": False,
                "error": "missing_task",
                "usage": 'python3 google_docs_router.py "<task>"',
            }
        )
        return

    task = " ".join(sys.argv[1:]).strip()
    log_router_call(task)

    try:
        result = route(task)
        emit(result)
    except Exception as err:
        emit(
            {
                "ok": False,
                "error": str(err),
                "traceback": traceback.format_exc(),
            }
        )


if __name__ == "__main__":
    main()
