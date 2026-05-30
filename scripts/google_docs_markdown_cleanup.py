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


def is_markdown_table_separator(line):
    value = line.strip()
    if not value:
        return False

    if "|" not in value:
        return False

    stripped = value.replace("|", "").replace("-", "").replace(":", "").replace(" ", "")
    return stripped == ""


def cleanup_markdown_document(document_id):
    service = docs_service()
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

        # Delete markdown table separator rows like |---|---|.
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

            # Replace full paragraph text with clean heading text.
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
        "ok": True,
        "operation": "markdown_cleanup",
        "document_id": document_id,
        "requests_count": len(requests),
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
