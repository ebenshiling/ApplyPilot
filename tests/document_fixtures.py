"""Explicit attestation of synthetic test text; never a production bypass."""
from pathlib import Path

from applypilot import canonical


def attest_text(conn,root,text):
    """Review literal toy assertions through the real human-action service."""
    for line in text.splitlines():
        line=canonical._normal(line)
        if not line or line in canonical.HEADINGS or line in canonical.NONFACTUAL:continue
        value=canonical.state(conn,root)
        if any(f["text"]==line and value["fact_states"][f["id"]]=="verified" for f in value["facts"]):continue
        value=canonical.action(conn,root,{"action":"save_fact","expected_revision":value["revision"],"fact":{"kind":"personal","text":line,"source_reference":"Explicit synthetic test assertion","source_quote":line}},actor="fixture_owner")
        canonical.action(conn,root,{"action":"verify_fact","expected_revision":value["revision"],"fact_id":value["facts"][-1]["id"],"human_reviewed":True},actor="fixture_owner")


def text_pdf(path,text):
    """Actual extractable ASCII PDF, so blank PDFs cannot satisfy round-trip tests."""
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
    writer=PdfWriter()
    page=writer.add_blank_page(width=595,height=842)
    font=DictionaryObject({NameObject("/Type"):NameObject("/Font"),NameObject("/Subtype"):NameObject("/Type1"),NameObject("/BaseFont"):NameObject("/Helvetica")})
    page[NameObject("/Resources")]=DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
    commands=["BT /F1 10 Tf 40 790 Td 12 TL"]
    for line in text.splitlines():
        escaped=line.replace("\\","\\\\").replace("(","\\(").replace(")","\\)")
        commands.append("("+escaped+") Tj T*")
    commands.append("ET")
    content=DecodedStreamObject()
    content.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")]=writer._add_object(content)
    writer.write(path)
    return Path(path)


def finalized_document(conn,root,path,job,kind="resume",*,pdf=False):
    path=Path(path)
    attest_text(conn,root,path.read_text(encoding="utf-8"))
    canonical.finalize(conn,root,path,job,kind=kind)
    if pdf:
        target=path.with_suffix(".pdf")
        text_pdf(target,path.read_text(encoding="utf-8"))
        canonical.bind_pdf(conn,root,path,target,job,kind=kind)
