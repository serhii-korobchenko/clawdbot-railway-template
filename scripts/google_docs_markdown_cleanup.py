#!/usr/bin/env python3
import json
import os
import re
import sys
import traceback

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/documents"]


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False))


def get_credentials():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()

    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError(
            "Missing Google OAuth env. Required: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN"
        )

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def docs_service():
    return build("docs", "v1", credentials=get_credentials())


def get_document(service, document_id):
    return service.documents().get(documentId=document_id).execute()


def iter_paragraphs(doc):
    content = doc.get("body", {}).get("content", [])

    for block in content:
        paragraph = block.get("paragraph")
        if not paragraph:
            continue

        elements = paragraph.get("elements", [])
        if not elements:
            continue

        start_index = elements[0].get("startIndex")
        end_index = elements[-1].get("endIndex")

        parts = []
        for element in elements:
            text_run = element.get("textRun")
            if text_run:
                parts.append(text_run.get("content", ""))

        text = "".join(parts)

        if start_index is None or end_index is None:
            continue

        yield {
            "start": start_index,
            "end": end_index,
            "text": text,
        }


def is_markdown_table_line(line):
    value = line.strip()
    return value.startswith("|") and value.endswith("|") and value.count("|") >= 2


def is_markdown_table_separator(line):
    value = line.strip()
    if not value:
        return False

    if "|" not in value:
        return False

    stripped = value.replace("|", "").replace("-", "").replace(":", "").replace(" ", "")
    return stripped == ""


def parse_markdown_table_row(line):
    value = line.strip()

    if value.startswith("|"):
        value = value[1:]

    if value.endswith("|"):
        value = value[:-1]

    cells = [clean_inline_markdown(cell.strip()) for cell in value.split("|")]
    return cells


def clean_inline_markdown(value):
    value = value.strip()

    # Remove common inline Markdown markers. This is intentionally conservative.
    value = re.sub(r"^`(.+)`$", r"\1", value)
    value = value.replace("**", "")
    value = value.replace("__", "")
    value = value.replace("`", "")

    # Convert [label](url) to "label — url" inside table cells.
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 — \2", value)

    return value.strip()


def find_markdown_table_blocks(doc):
    paragraphs = list(iter_paragraphs(doc))
    blocks = []

    i = 0
    while i < len(paragraphs):
        line = paragraphs[i]["text"].strip()

        if not is_markdown_table_line(line):
            i += 1
            continue

        start_i = i
        rows_raw = []

        while i < len(paragraphs) and is_markdown_table_line(paragraphs[i]["text"].strip()):
            rows_raw.append(paragraphs[i])
            i += 1

        # A valid markdown table should usually have:
        # header row + separator row + at least one data row.
        if len(rows_raw) < 3:
            continue

        has_separator = any(is_markdown_table_separator(row["text"].strip()) for row in rows_raw[1:3])
        if not has_separator:
            continue

        parsed_rows = []
        for row in rows_raw:
            row_text = row["text"].strip()
            if is_markdown_table_separator(row_text):
                continue
            parsed_rows.append(parse_markdown_table_row(row_text))

        if len(parsed_rows) < 2:
            continue

        column_count = len(parsed_rows[0])
        if column_count < 2:
            continue

        normalized_rows = []
        valid = True

        for row in parsed_rows:
            if len(row) > column_count:
                valid = False
                break

            if len(row) < column_count:
                row = row + [""] * (column_count - len(row))

            normalized_rows.append(row)

        if not valid:
            continue

        blocks.append(
            {
                "start": rows_raw[0]["start"],
                "end": rows_raw[-1]["end"],
                "rows": normalized_rows,
                "row_count": len(normalized_rows),
                "column_count": column_count,
            }
        )

    return blocks


def find_inserted_table(doc, near_index):
    candidates = []

    for block in doc.get("body", {}).get("content", []):
        if not block.get("table"):
            continue

        start = block.get("startIndex")
        end = block.get("endIndex")

        if start is None or end is None:
            continue

        # Inserted tables usually start at or very close to the insertion index.
        if start >= near_index - 2:
            candidates.append((abs(start - near_index), start, block))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def cell_insert_index(cell):
    content = cell.get("content", [])

    for block in content:
        paragraph = block.get("paragraph")
        if not paragraph:
            continue

        elements = paragraph.get("elements", [])
        if not elements:
            continue

        start = elements[0].get("startIndex")
        if start is not None:
            return start

    return None


def fill_google_docs_table(service, document_id, table_block, rows):
    table = table_block.get("table", {})
    table_rows = table.get("tableRows", [])

    insert_requests = []

    for r_idx, row in enumerate(rows):
        if r_idx >= len(table_rows):
            continue

        table_cells = table_rows[r_idx].get("tableCells", [])

        for c_idx, value in enumerate(row):
            if c_idx >= len(table_cells):
                continue

            value = str(value or "").strip()
            if not value:
                continue

            idx = cell_insert_index(table_cells[c_idx])
            if idx is None:
                continue

            insert_requests.append(
                {
                    "insertText": {
                        "location": {
                            "index": idx,
                        },
                        "text": value,
                    }
                }
            )

    # Reverse by insert index so later insertions do not shift earlier indexes.
    insert_requests.sort(
        key=lambda req: req["insertText"]["location"]["index"],
        reverse=True,
    )

    if insert_requests:
        service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": insert_requests},
        ).execute()

    return len(insert_requests)


def convert_markdown_tables(service, document_id):
    doc = get_document(service, document_id)
    table_blocks = find_markdown_table_blocks(doc)

    if not table_blocks:
        return {
            "tables_converted": 0,
            "table_cell_insertions": 0,
        }

    # Process bottom-to-top to keep paragraph indexes stable.
    table_blocks.sort(key=lambda item: item["start"], reverse=True)

    converted = 0
    cell_insertions = 0

    for block in table_blocks:
        start = block["start"]
        end = block["end"]
        rows = block["rows"]

        requests = [
            {
                "deleteContentRange": {
                    "range": {
                        "startIndex": start,
                        "endIndex": end,
                    }
                }
            },
            {
                "insertTable": {
                    "rows": block["row_count"],
                    "columns": block["column_count"],
                    "location": {
                        "index": start,
                    },
                }
            },
        ]

        service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": requests},
        ).execute()

        refreshed = get_document(service, document_id)
        table_block = find_inserted_table(refreshed, start)

        if not table_block:
            continue

        cell_insertions += fill_google_docs_table(service, document_id, table_block, rows)
        converted += 1

    return {
        "tables_converted": converted,
        "table_cell_insertions": cell_insertions,
    }


def cleanup_headings_and_bullets(service, document_id):
    doc = get_document(service, document_id)

    requests = []

    # Process from bottom to top to keep indexes stable.
    paragraphs = list(iter_paragraphs(doc))
    paragraphs.sort(key=lambda item: item["start"], reverse=True)

    for para in paragraphs:
        raw_text = para["text"]
        line = raw_text.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            continue

        start = para["start"]
        end = para["end"]

        # Delete leftover markdown table separator rows like |---|---|.
        if is_markdown_table_separator(stripped):
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": start,
                            "endIndex": end,
                        }
                    }
                }
            )
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            hashes = heading_match.group(1)
            heading_text = heading_match.group(2).strip()

            if len(hashes) == 1:
                named_style = "HEADING_1"
            elif len(hashes) == 2:
                named_style = "HEADING_2"
            else:
                named_style = "HEADING_3"

            content_end = end - 1 if raw_text.endswith("\n") else end

            requests.append(
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": start,
                            "endIndex": content_end,
                        }
                    }
                }
            )
            requests.append(
                {
                    "insertText": {
                        "location": {
                            "index": start,
                        },
                        "text": heading_text,
                    }
                }
            )
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": start,
                            "endIndex": start + len(heading_text),
                        },
                        "paragraphStyle": {
                            "namedStyleType": named_style,
                            "spaceAbove": {
                                "magnitude": 12,
                                "unit": "PT",
                            },
                            "spaceBelow": {
                                "magnitude": 6,
                                "unit": "PT",
                            },
                        },
                        "fields": "namedStyleType,spaceAbove,spaceBelow",
                    }
                }
            )
            continue

        bullet_match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bullet_match:
            bullet_text = bullet_match.group(1).strip()
            content_end = end - 1 if raw_text.endswith("\n") else end

            requests.append(
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": start,
                            "endIndex": content_end,
                        }
                    }
                }
            )
            requests.append(
                {
                    "insertText": {
                        "location": {
                            "index": start,
                        },
                        "text": bullet_text,
                    }
                }
            )
            requests.append(
                {
                    "createParagraphBullets": {
                        "range": {
                            "startIndex": start,
                            "endIndex": start + len(bullet_text),
                        },
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )

    if requests:
        service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": requests},
        ).execute()

    return {
        "text_cleanup_requests": len(requests),
    }


def cleanup_markdown_document(document_id):
    service = docs_service()

    table_result = convert_markdown_tables(service, document_id)
    text_result = cleanup_headings_and_bullets(service, document_id)

    return {
        "ok": True,
        "operation": "markdown_cleanup",
        "document_id": document_id,
        **table_result,
        **text_result,
    }


def main():
    if len(sys.argv) < 2:
        emit(
            {
                "ok": False,
                "error": "missing_document_id",
                "usage": "python3 google_docs_markdown_cleanup.py <document_id>",
            }
        )
        return

    document_id = sys.argv[1].strip()

    try:
        emit(cleanup_markdown_document(document_id))
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
