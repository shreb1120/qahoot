"""Upload validation. Every rejection must be a real page with the user's input
still in it, never a lost form or a raw error."""
import io
import pytest


def _file(name="call.mp3", data=b"ID3" + b"\x00" * 128):
    return (io.BytesIO(data), name)


def _form(tenants, **over):
    d = {"agent_id": tenants.a["agent"], "alv_id": "12345", "call_date": "2026-08-01",
         "client_phone": "8005551234", "file": _file()}
    d.update(over)
    return d


def test_valid_upload_is_accepted(tenants, monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: None)
    r = tenants.a_admin.post("/calls/upload", data=_form(tenants),
                             content_type="multipart/form-data")
    assert r.status_code == 302 and "/calls/" in r.headers["Location"]


@pytest.mark.parametrize("field,value,message", [
    ("agent_id", "", "Select which agent"),
    ("alv_id", "", "ALV or internal ID"),
    ("call_date", "", "date the call took place"),
    ("call_date", "2099-01-01", "cannot have happened in the future"),
    ("call_date", "not-a-date", "not a valid date"),
])
def test_missing_or_bad_fields_are_rejected_with_a_message(tenants, field, value, message):
    r = tenants.a_admin.post("/calls/upload", data=_form(tenants, **{field: value}),
                             content_type="multipart/form-data")
    assert r.status_code == 400
    assert message in r.get_data(as_text=True)


def test_rejection_preserves_what_the_user_typed(tenants):
    r = tenants.a_admin.post("/calls/upload",
                             data=_form(tenants, alv_id="SPECIAL-9", agent_id=""),
                             content_type="multipart/form-data")
    body = r.get_data(as_text=True)
    assert "SPECIAL-9" in body, "the user's typed ALV was discarded on error"
    assert "Acme Agent" in body, "the agent dropdown was emptied on error"


def test_missing_file_is_rejected(tenants):
    data = _form(tenants)
    data.pop("file")
    r = tenants.a_admin.post("/calls/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "Choose a call recording" in r.get_data(as_text=True)


def test_unsupported_file_type_is_rejected(tenants):
    r = tenants.a_admin.post("/calls/upload",
                             data=_form(tenants, file=_file("notes.txt", b"hello")),
                             content_type="multipart/form-data")
    assert r.status_code == 400
    assert "not supported" in r.get_data(as_text=True)


def test_empty_file_is_rejected(tenants, monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: None)
    r = tenants.a_admin.post("/calls/upload", data=_form(tenants, file=_file("call.mp3", b"")),
                             content_type="multipart/form-data")
    assert r.status_code == 400
    assert "empty" in r.get_data(as_text=True)


def test_an_agent_from_another_org_is_rejected(tenants):
    """The agent id is user-supplied; it must be validated against the session's org."""
    r = tenants.a_admin.post("/calls/upload", data=_form(tenants, agent_id=tenants.b["agent"]),
                             content_type="multipart/form-data")
    assert r.status_code == 400
    assert "no longer available" in r.get_data(as_text=True)


def test_non_latin_filename_still_gets_a_usable_name(tenants, monkeypatch, db):
    """secure_filename() strips non-ASCII and can return an empty string."""
    import pipeline
    from models import Call
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: None)
    r = tenants.a_admin.post("/calls/upload",
                             data=_form(tenants, file=_file("録音.mp3")),
                             content_type="multipart/form-data")
    assert r.status_code == 302
    call = db.query(Call).filter_by(org_id=tenants.a["org"]).order_by(Call.upload_date.desc()).first()
    assert call.filename and not call.filename.startswith("."), call.filename


def test_a_failed_pipeline_marks_the_call_errored(tenants, monkeypatch, db):
    """Otherwise the call sits in 'pending' forever with nothing working on it."""
    import pipeline
    from models import Call

    def boom(**kw):
        raise RuntimeError("no api key")
    monkeypatch.setattr(pipeline, "spawn", boom)

    r = tenants.a_admin.post("/calls/upload", data=_form(tenants, alv_id="ERR-1"),
                             content_type="multipart/form-data")
    assert r.status_code == 302
    call = db.query(Call).filter_by(alv_id="ERR-1").first()
    assert call.status == "error" and call.error_message
