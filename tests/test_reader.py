import io
from datetime import datetime

import pytest
from openpyxl import Workbook

from app.ingest.reader import UnsupportedFileError, read_file


def test_csv_with_bom_and_short_rows():
    content = "﻿Name,Email,Country\nAsha,asha@x.com,IN\nRavi,ravi@x.com\n".encode()
    parsed = read_file("clients.CSV", content)
    assert parsed.headers == ["Name", "Email", "Country"]
    assert parsed.rows[0] == {"Name": "Asha", "Email": "asha@x.com", "Country": "IN"}
    assert parsed.rows[1]["Country"] == ""  # short row padded


def test_xlsx_values_become_strings():
    wb = Workbook()
    ws = wb.active
    ws.append(["Name", "Phone", "Joined"])
    ws.append(["Asha", 9876543210, datetime(2024, 3, 1)])
    ws.append(["Ravi", None, None])
    buf = io.BytesIO()
    wb.save(buf)

    parsed = read_file("clients.xlsx", buf.getvalue())
    assert parsed.rows[0] == {"Name": "Asha", "Phone": "9876543210", "Joined": "2024-03-01"}
    assert parsed.rows[1] == {"Name": "Ravi", "Phone": "", "Joined": ""}


def test_empty_csv():
    parsed = read_file("empty.csv", b"")
    assert parsed.headers == [] and parsed.rows == []


def test_unsupported_extension():
    with pytest.raises(UnsupportedFileError):
        read_file("clients.pdf", b"%PDF")
