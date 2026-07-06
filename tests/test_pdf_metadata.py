from pathlib import Path

from pypdf import PdfReader, PdfWriter

from applypilot.scoring import pdf


def _write_stub_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=144, height=144)
    with path.open("wb") as fh:
        writer.write(fh)


def test_convert_to_pdf_writes_job_id_metadata(monkeypatch, tmp_path: Path) -> None:
    txt_path = tmp_path / "resume.txt"
    txt_path.write_text(
        "Jane Doe\nApplication Support Engineer\nLondon, UK\njane@example.com\n\nSUMMARY\nHelpful summary\n\nEXPERIENCE\nRole\nCompany\n- Did the thing\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pdf, "render_pdf", lambda html, output_path: _write_stub_pdf(Path(output_path)))

    out = pdf.convert_to_pdf(
        txt_path,
        pdf_metadata=pdf.build_pdf_metadata(
            "resume",
            personal={"full_name": "Jane Alexandra Doe", "preferred_name": "Jane"},
            job={"rowid": 2586, "title": "Application Support Engineer", "site": "linkedin"},
        ),
    )

    meta = PdfReader(str(out)).metadata
    assert meta.get("/Author") == "Jane Doe"
    assert meta.get("/Title") == "Jane Doe CV"
    assert meta.get("/ApplyPilotJobId") == "J2586"
    assert "J2586" in str(meta.get("/Subject") or "")
    assert "job-id:J2586" in str(meta.get("/Keywords") or "")


def test_pdf_job_id_reads_custom_metadata(monkeypatch, tmp_path: Path) -> None:
    txt_path = tmp_path / "resume.txt"
    txt_path.write_text(
        "Jane Doe\nApplication Support Engineer\nLondon, UK\njane@example.com\n\nSUMMARY\nHelpful summary\n\nEXPERIENCE\nRole\nCompany\n- Did the thing\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pdf, "render_pdf", lambda html, output_path: _write_stub_pdf(Path(output_path)))

    out = pdf.convert_to_pdf(
        txt_path,
        pdf_metadata=pdf.build_pdf_metadata(
            "resume",
            personal={"full_name": "Jane Alexandra Doe", "preferred_name": "Jane"},
            job={"rowid": 4880, "title": "2nd Line Technical Analyst", "site": "linkedin"},
        ),
    )

    assert pdf.pdf_job_id(out) == "J4880"
    meta = pdf.read_pdf_metadata(out)
    assert meta["/ApplyPilotJobId"] == "J4880"
